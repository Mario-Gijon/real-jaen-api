"""Loopback-only pilot transport tests and split-container integration instruments."""
import argparse
import asyncio
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import tempfile
import unittest
import uuid

from acquisition.pilot_transport import PilotTransport,PilotStopped
from persistencia.bundle_v2 import PATHS,encode,parse,sha

class Clock:
    def __init__(self):self.value=datetime(2026,10,28,12,tzinfo=timezone.utc).timestamp()
    def __call__(self):return self.value
    async def sleep(self,seconds):self.value+=seconds;await asyncio.sleep(0)

@contextmanager
def server(routes):
    hits=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            path=self.path.removeprefix('/api/v1');hits.append(path)
            actions=routes.get(path,[(404,{}, {})])
            status,headers,body=actions.pop(0) if len(actions)>1 else actions[0]
            raw=body if isinstance(body,bytes) else encode(body)
            self.send_response(status)
            for k,v in headers.items():self.send_header(k,v)
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{http.server_port}/api/v1',hits
    finally:http.shutdown();http.server_close();thread.join()

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.clock=Clock()
        self.context=dict(job_id=str(uuid.uuid4()),request_id=str(uuid.uuid4()),inventory_sha256='a'*64)
    def transport(self,url,**kwargs):return PilotTransport(self.root,loopback_url=url,clock=self.clock,sleep=self.clock.sleep,**self.context,**kwargs)
    def get(self,t,path=PATHS[1]):
        async def work():
            async with t:return await t.get(path)
        return asyncio.run(work())
    def test_default_disabled_and_unauthorized_urls(self):
        with self.assertRaises(PilotStopped):PilotTransport(self.root,**self.context)
        with self.assertRaises(ValueError):self.transport('http://example.invalid/api/v1')
    def test_success_counts_before_socket_and_dedup(self):
        with server({PATHS[1]:[(200,{}, {'event':{'id':16599363}})]}) as (url,hits),self.transport(url) as t:
            original=t._request
            def proof(path):
                saved=parse(t.path.read_bytes());self.assertEqual(saved['attempts'][-1]['status'],None)
                self.assertEqual(saved['attempts'][-1]['path'],path);return original(path)
            t._request=proof
            self.get(t);self.get(t)
            self.assertEqual(hits,[PATHS[1]]);self.assertEqual(len(t.state['attempts']),1)
    def test_retry_after_503_counts_every_attempt_and_waits(self):
        with server({PATHS[1]:[(503,{'Retry-After':'60'},{}),(200,{}, {'event':{'id':16599363}})]}) as (url,hits),self.transport(url) as t:
            self.assertEqual(self.get(t).status,'ok');self.assertEqual(len(hits),2)
            a,b=t.state['attempts'];self.assertGreaterEqual(datetime.fromisoformat(b['sent_at']).timestamp()-datetime.fromisoformat(a['finished_at']).timestamp(),60)
    def test_budget_exhausted_before_next_attempt(self):
        with server({PATHS[1]:[(503,{}, {})]}) as (url,hits),self.transport(url,max_attempts=1) as t:
            with self.assertRaises(PilotStopped):self.get(t)
            self.assertEqual(len(hits),1)
    def test_refusal_persisted_across_new_jobs(self):
        for code in (403,429):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as root:
                self.root=Path(root)
                with server({PATHS[1]:[(code,{'Retry-After':'3600'}, {})]}) as (url,hits):
                    with self.transport(url) as t:
                        with self.assertRaises(PilotStopped):self.get(t)
                    self.context['job_id']=str(uuid.uuid4())
                    with self.transport(url) as t:
                        with self.assertRaises(PilotStopped):self.get(t)
                    self.assertEqual(len(hits),1)
    def test_redirect_does_not_contact_target_or_retry(self):
        with server({PATHS[1]:[(302,{'Location':'http://example.invalid/never'}, {})]}) as (url,hits),self.transport(url) as t:
            self.assertEqual(self.get(t).status,'failed');self.assertEqual(len(hits),1)
            with self.assertRaises(PilotStopped):self.get(t)
            self.assertEqual(len(hits),1)
    def test_cancel_and_unplanned_path_have_zero_requests(self):
        with server({}) as (url,hits),self.transport(url,cancelled=lambda:True) as t:
            with self.assertRaises(InterruptedError):self.get(t)
            self.assertEqual(hits,[])
        with server({}) as (url,hits),self.transport(url) as t:
            with self.assertRaises(PilotStopped):self.get(t,'/event/1')
            self.assertEqual(hits,[])
    def test_long_retry_after_does_not_get_shortened(self):
        with server({PATHS[1]:[(503,{'Retry-After':'3600'}, {})]}) as (url,hits),self.transport(url) as t:
            with self.assertRaises(PilotStopped):self.get(t)
            self.assertEqual(len(hits),1)
    def test_lock_concurrency_and_interrupted_attempt_budget(self):
        with server({PATHS[1]:[(200,{}, {'event':{'id':16599363}})]}) as (url,hits):
            with self.transport(url) as t:
                with self.assertRaises(BlockingIOError):
                    with self.transport(url):pass
                t.state['attempts']=[dict(number=1,path=PATHS[1],sent_at=t.state['started_at'],finished_at=None,status=None,payload_sha256=None)]
                from acquisition.profile import atomic_json
                atomic_json(t.path,t.state)
            with self.transport(url) as t:
                self.assertEqual(t.state['attempts'][0]['status'],0)
                self.get(t);self.assertEqual(len(t.state['attempts']),2)
                self.assertEqual(len(hits),1)
    def test_journal_full_stops_before_socket_and_releases_owner(self):
        from unittest.mock import patch
        with server({}) as (url,hits):
            t=self.transport(url)
            with patch('acquisition.pilot_transport.atomic_json',side_effect=OSError('ENOSPC')):
                with self.assertRaises(OSError):
                    with t:pass
            self.assertIsNone(t.owner)
            with self.transport(url):pass
            self.assertEqual(hits,[])

    def test_response_evidence_failure_does_not_retry(self):
        from unittest.mock import patch
        real_open=Path.open
        def fail(path,*args,**kwargs):
            if path.suffix=='.body':raise OSError('ENOSPC')
            return real_open(path,*args,**kwargs)
        with server({PATHS[1]:[(200,{}, {'event':{'id':16599363}})]}) as (url,hits),self.transport(url) as t:
            with patch.object(Path,'open',fail):
                with self.assertRaises(PilotStopped):self.get(t)
            self.assertEqual(len(hits),1)

    def test_existing_success_is_not_queried_again_after_restart(self):
        with server({PATHS[1]:[(200,{}, {'event':{'id':16599363}})]}) as (url,hits):
            with self.transport(url) as t:self.get(t)
            with self.transport(url) as t:
                with self.assertRaises(PilotStopped):self.get(t)
            self.assertEqual(len(hits),1)

    def test_empty_404_and_parse_never_confirm_absence(self):
        for code,body in ((404,{}),(200,{'incidents':[]}),(200,b'invalid-json')):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as root:
                self.root=Path(root)
                with server({PATHS[-1]:[(code,{},body)]}) as (url,hits),self.transport(url) as t:
                    result=self.get(t,PATHS[-1]);self.assertNotEqual(result.status,'ok')
                    with self.assertRaises(PilotStopped):self.get(t,PATHS[-1])
                    self.assertEqual(len(hits),1)

class ArtifactFailures(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.clock=Clock()
        from sofascore_scraper.store import Store
        self.store=Store(self.root/'cache');self.addCleanup(self.store.close)
        from persistencia.bundle_v2 import fingerprint
        self.event=dict(id=16599363,tournament=dict(id=94191,uniqueTournament=dict(id=17073),category=dict(sport=dict(slug='football',id=1))),
                        season=dict(id=97382),homeTeam=dict(id=2841,name='Fixture home'),awayTeam=dict(id=135672,name='Fixture away'),
                        status=dict(type='finished',code=100),startTimestamp=int(self.clock()-86400),roundInfo=dict(round=1))
        b=dict(schema=1,generation_id=str(uuid.uuid4()),teams=[2841,135672,*range(90001,90019)],
               events=[dict(id=16599363,current=True,replaced_by=None,home=2841,away=135672,status='finished',tournament_id=94191,season_id=97382)],records=[])
        self.baseline=dict(b,sha256=fingerprint(b))
    def test_backup_failure_prevents_all_requests_and_sealing(self):
        from acquisition.pilot_bundle import collect_pilot
        from unittest.mock import patch
        with server({}) as (url,hits),PilotTransport(self.root/'jobs',job_id=str(uuid.uuid4()),request_id=str(uuid.uuid4()),
                    inventory_sha256=self.baseline['sha256'],loopback_url=url,clock=self.clock,sleep=self.clock.sleep) as t:
            with patch.object(self.store.backup,'create',side_effect=OSError('ENOSPC')):
                with self.assertRaises(OSError):collect_pilot(self.store,t,self.baseline,self.root/'bundle.json')
            self.assertEqual(hits,[]);self.assertFalse((self.root/'bundle.json').exists())
    def test_correction_outside_fixed_pilot_stops_before_more_queries(self):
        from acquisition.pilot_bundle import collect_pilot
        from persistencia.bundle_v2 import fingerprint
        import copy
        old=copy.deepcopy(self.event);old['id']=16599359
        self.baseline['events'].append(dict(self.baseline['events'][0],id=old['id']))
        self.baseline['records'].append(dict(record=dict(event_id=old['id'],key='event',sub='',fetched_at='2026-10-21T12:00:00+00:00',payload=old),source={}))
        self.baseline['sha256']=fingerprint({k:v for k,v in self.baseline.items() if k!='sha256'})
        corrected=copy.deepcopy(old);corrected['status']['type']='postponed'
        with server({PATHS[0]:[(200,{}, {'events':[self.event,corrected]})]}) as (url,hits),PilotTransport(
                    self.root/'jobs',job_id=str(uuid.uuid4()),request_id=str(uuid.uuid4()),inventory_sha256=self.baseline['sha256'],
                    loopback_url=url,clock=self.clock,sleep=self.clock.sleep) as t:
            with self.assertRaisesRegex(PilotStopped,'outside fixed pilot'):
                collect_pilot(self.store,t,self.baseline,self.root/'bundle.json')
            self.assertEqual(hits,[PATHS[0]]);self.assertFalse((self.root/'bundle.json').exists())

    def test_partial_sporting_acquisition_never_seals(self):
        from acquisition.pilot_bundle import collect_pilot
        routes={PATHS[0]:[(200,{}, {'events':[self.event]})],PATHS[1]:[(200,{}, {'event':self.event})]}
        # No section has an approved absence rule. A 404 must leave the whole acquisition incomplete.
        with server(routes) as (url,hits),PilotTransport(self.root/'jobs',job_id=str(uuid.uuid4()),request_id=str(uuid.uuid4()),
                    inventory_sha256=self.baseline['sha256'],loopback_url=url,clock=self.clock,sleep=self.clock.sleep) as t:
            with self.assertRaises(PilotStopped):collect_pilot(self.store,t,self.baseline,self.root/'bundle.json')
            self.assertFalse((self.root/'bundle.json').exists());self.assertTrue(hits)


def acquisition_stage(exchange,root):
    from acquisition.pilot_bundle import seed_cache,collect_pilot,producer_rules
    from sofascore_scraper.store import Store
    baseline=parse((exchange/'inventory.json').read_bytes())
    prior={(s['record']['event_id'],s['record']['key']):s['record'] for s in baseline['records']}
    # Only archived originals, no fabricated sporting values. Synthetic HTTP is explicitly test transport.
    target=prior[16599363,'event']['payload']
    round_events=[v['record']['payload'] for v in baseline['records'] if v['record']['key']=='event' and v['record']['payload'].get('roundInfo',{}).get('round')==1]
    routes={PATHS[0]:[(200,{},dict(events=round_events))],PATHS[1]:[(200,{},dict(event=target))]}
    routes.update({p:[(200,{},prior[16599363,p.rsplit('/',1)[1]]['payload'])] for p in PATHS[2:]})
    job,request=str(uuid.uuid4()),str(uuid.uuid4());clock=Clock()
    with closing(Store(root/'cache')) as store:
        seed_cache(store,baseline)
        with server(routes) as (url,hits),PilotTransport(root/'jobs',job_id=job,request_id=request,inventory_sha256=baseline['sha256'],loopback_url=url,clock=clock,sleep=clock.sleep) as transport:
            descriptor=collect_pilot(store,transport,baseline,exchange/'bundle.json')
            self_check=collect_pilot(store,transport,baseline,exchange/'bundle.json')
            assert self_check==descriptor
            assert len(hits)==5 and set(hits)==set(PATHS) and hits[:2]==list(PATHS[:2]),hits
            metadata=dict(descriptor=descriptor,context=[job,request],rules_sha256=producer_rules(),attempts=len(hits),external_requests=0)
            (exchange/'bundle-meta.json').write_bytes(encode(metadata))
            first=encode(dict(stage='acquisition',requests=hits,attempts=len(hits),bytes=descriptor['bytes'],retained=[e['id'] for e in baseline['events'] if not e['current']])).decode()
            print(first)
        clock.value+=10
        job2,request2=str(uuid.uuid4()),str(uuid.uuid4())
        with server(routes) as (url,hits2),PilotTransport(root/'jobs',job_id=job2,request_id=request2,inventory_sha256=baseline['sha256'],loopback_url=url,clock=clock,sleep=clock.sleep) as second:
            desc2=collect_pilot(store,second,baseline,exchange/'bundle-second.json')
            assert len(hits2)==5 and set(hits2)==set(PATHS)
            (exchange/'second-meta.json').write_bytes(encode(dict(descriptor=desc2,context=[job2,request2],rules_sha256=producer_rules(),attempts=len(hits2))))

def sdk_acquisition_stage(exchange,root,downloaded):
    """Como acquisition_stage, pero cada petición sale por el flujo normal del SDK (curl y, ante el challenge, el
    navegador) con el adaptador de adquisición, el perfil gestionado y el sandbox exigido, contra el sitio de prueba
    de 127.0.0.1 que sirve los cuerpos descargados de SofaScore el 27-10-2026. Sin red."""
    from acquisition.sdk_adapter import SdkAdapter,configure_environment
    configure_environment(root/'sdk')  # antes de importar el SDK
    from acquisition.profile import Profile
    from acquisition.sdk_transport import SdkTransport
    from acquisition.pilot_bundle import seed_cache,collect_pilot,producer_rules
    from sofascore_scraper.store import Store
    from sdk_fixture import FixtureSite,load_downloaded
    baseline=parse((exchange/'inventory.json').read_bytes())
    managed=root/'managed-profile';managed.mkdir(mode=0o700)
    jobs=[(str(uuid.uuid4()),str(uuid.uuid4())) for _ in range(2)]
    with Profile(managed) as owner:
        owner.initialize(confirm_new=True)
        with closing(Store(root/'cache')) as store:
            seed_cache(store,baseline)
            with FixtureSite(load_downloaded(downloaded),scenario='challenge') as site,owner.session() as profile:
                owner.allow_browser(profile)
                adapter=SdkAdapter(profile,PATHS,api=site.api,site=site.origin,require_sandbox=True,local_test=True).install()
                descriptors=[]
                try:
                    for (job,request),name in zip(jobs,('bundle.json','bundle-second.json')):
                        with SdkTransport(root/'jobs',adapter=adapter,job_id=job,request_id=request,
                                          inventory_sha256=baseline['sha256'],loopback_url=site.api) as transport:
                            descriptors.append(collect_pilot(store,transport,baseline,exchange/name))
                            assert collect_pilot(store,transport,baseline,exchange/name)==descriptors[-1]  # sellado: sin red
                finally:
                    adapter.close()
                hits=site.api_hits();report=adapter.report()
            assert report['stopped'] is None,report['stopped']
            assert report['sandbox']=={'namespaces':True,'seccomp':True,'proc_verified':True},report['sandbox']
            assert [s['user_data_dir'] for s in report['sessions']]==[str(profile)]
            assert {h['path'] for h in hits}=={'/api/v1'+p for p in PATHS} and len(hits)==len([s for s in report['sends'] if s['sent']])
            assert report['browser']['api']==len([s for s in report['sends'] if s['sent'] and s['channel']=='bridge'])
        clean=owner.verify()  # tras la sesión: sin singletons ni sesión abierta
    for (job,request),descriptor,name in zip(jobs,descriptors,('bundle-meta.json','second-meta.json')):
        meta=dict(descriptor=descriptor,context=[job,request],rules_sha256=producer_rules(),attempts=5,external_requests=0)
        (exchange/name).write_bytes(encode(meta))
    summary=dict(stage='acquisition-sdk',requests=[h['path'] for h in hits],sends=len(hits),
                 vias=[(r['path'],r['via'],r['status']) for r in report['requests']],sandbox=report['sandbox'],
                 browser=report['browser'],sessions=report['sessions'],managed_profile=clean,
                 events=[e['kind'] for e in report['events']],bytes=[d['bytes'] for d in descriptors])
    (exchange/'sdk-report.json').write_bytes(encode(summary));print(encode(summary).decode())

if __name__=='__main__':
    import sys
    if len(sys.argv)>1 and sys.argv[1]=='collect':acquisition_stage(Path('/exchange'),Path('/profiles'))
    elif len(sys.argv)>1 and sys.argv[1]=='collect-sdk':sdk_acquisition_stage(Path('/exchange'),Path('/profiles'),Path(sys.argv[2]))
    else:unittest.main(verbosity=2)
