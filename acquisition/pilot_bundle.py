"""Sealed pilot bundles from the real SDK cache/export API. No sporting SQLite access."""
from datetime import datetime
import base64
import json
import os
from pathlib import Path
import shutil

from .pilot_transport import PilotStopped, iso
from .profile import sync_dir
from persistencia.bundle_v2 import PATHS, PILOT, SECTIONS, check_inventory, encode, fingerprint, record, sha, verify

REVISION='611e436a1528b0f487aa56a045f023e8ae8ae098'

def producer_rules():
    names=('pilot_bundle.py','pilot_transport.py','sofascore_offline.py')
    sources={n:sha(Path(__file__).with_name(n).read_bytes()) for n in names}
    return fingerprint(dict(sources=sources,scraper=REVISION,policy=dict(window_s=259200,interval_s=21600)))

def seed_cache(store,baseline):
    """Explicit initialization from backend-selected evidence, not an empty-profile fallback."""
    from sofascore_scraper.slices import Outcome
    expected=check_inventory(baseline);grouped={}
    for source in baseline['records']:
        row=source['record']
        if row['event_id'] not in expected or row['key']=='listing':continue
        if row['fetched_at'] is None:raise PilotStopped('Raw evidence without provenance needs explicit initialization')
        grouped.setdefault(row['event_id'],{})[row['key']]=Outcome('ok',data=row['payload'],fetched_at=datetime.fromisoformat(row['fetched_at']))
    with store.lease('writer',purpose='explicit-test-cache-initialization'):
        for ident,outcomes in grouped.items():store.events.put(ident,outcomes)

class PilotListing:
    def __init__(self,store,baseline):self.store,self.baseline=store,baseline
    async def run(self,item,get,write):
        from sofascore_scraper.services.pipeline import ItemResult
        from sofascore_scraper.services.listing import ScheduleLister, round_is_complete
        response=await get(PATHS[0],retries=1)
        if response.status!='ok':return ItemResult(item,'failed',response.reason,event=response)
        data=response.data
        if not isinstance(data,dict) or not isinstance(data.get('events'),list):raise PilotStopped('Malformed round')
        expected=check_inventory(self.baseline)
        scoped=[e for e in data['events'] if e.get('tournament',{}).get('id')==94191]
        if PILOT not in {e.get('id') for e in scoped}:raise PilotStopped('Pilot missing from its verified round')
        if any(e.get('id') not in expected for e in scoped):raise PilotStopped('New event: another preflight required')
        import importar
        prior={(s['record']['event_id'],s['record']['key']):s['record'] for s in self.baseline['records']}
        for event in scoped:
            if event['id']==PILOT:continue
            original=prior.get((event['id'],'event'))
            if original is None or fingerprint(importar.rasgos_evento(original['payload']))!=fingerprint(importar.rasgos_evento(event)):
                raise PilotStopped('Round correction outside fixed pilot: another preflight required')
        lister=ScheduleLister(self.store,only_finished=False,concurrency=1)
        await write(lambda:lister.save_page(17073,97382,'round_1',data,meta={'complete':round_is_complete(data)},fetched_at=response.fetched_at))
        return ItemResult(item,'ok')

def collect_pilot(store,transport,baseline,output,*,cancelled=lambda:False):
    """One targeted pilot, not a historical rotation. Caller explicitly owns the transport."""
    from sofascore_scraper.services.pipeline import FetchPipeline
    from sofascore_scraper.services.planning import WorkItem
    from sofascore_scraper.store import Ref
    expected=check_inventory(baseline)
    if PILOT not in expected or not expected[PILOT]['current']:raise PilotStopped('Pilot is not current in this inventory')
    output=Path(output)
    if output.exists():
        body=output.read_bytes();descriptor=dict(bytes=len(body),sha256=sha(body))
        verify(body,descriptor,baseline,context=(transport.context['job_id'],transport.context['request_id']),expected_rules=producer_rules(),test_transport=transport.mode=='loopback-fixture')
        return descriptor # Exact repeat of a sealed job, no network or writes.
    required=3*len(encode(baseline))+64*1024*1024
    if shutil.disk_usage(output.parent).free<required:raise PilotStopped('Insufficient space before backup/download')
    results=[]
    with store.lease('writer',purpose='fixed-pilot-download-and-export'):
        backup=store.backup.create(scope='all')
        if not store.backup.verify(backup.name).ok:raise PilotStopped('Cache backup verification failed')
        # Preserve even partial new evidence before any later run can overwrite cache files.
        async def run():
            pipeline=FetchPipeline(store,client=transport,concurrency=1,selection=SECTIONS,listing=PilotListing(store,baseline))
            listing=WorkItem(Ref.season(17073,97382),'listing',(('pilot_round_1',''),),None,'fixed pilot round')
            first=await pipeline.run([listing],cancelled=cancelled)
            if first.failed or first.cancelled or first.skipped or cancelled():raise PilotStopped('Calendar acquisition incomplete')
            detail=WorkItem(Ref.event(PILOT),'refill',tuple((k,'') for k in SECTIONS),'football','explicit pilot authorization')
            second=await pipeline.run([detail],cancelled=cancelled)
            results.extend(second.results)
            if second.failed or second.cancelled or second.skipped or cancelled():raise PilotStopped('Pilot acquisition incomplete')
            if any(o.status!='ok' for result in second.results for o in result.slices.values()):raise PilotStopped('Sporting resource incomplete; no bundle publication')
        import asyncio
        try:
            asyncio.run(run())
            body=assemble(store,baseline,transport,output.parent)
            descriptor=dict(bytes=len(body),sha256=sha(body))
            verify(body,descriptor,baseline,context=(transport.context['job_id'],transport.context['request_id']),
                   expected_rules=producer_rules(),test_transport=transport.mode=='loopback-fixture')
        except BaseException:
            # Preserve partial evidence; never turn it into a publishable bundle.
            partial=store.backup.create(scope='all')
            if not store.backup.verify(partial.name).ok:raise PilotStopped('Partial cache backup failed; acquisition stopped')
            raise
        # Seal only after verified post-acquisition backup. A failed maintenance step stops here.
        complete=store.backup.create(scope='all')
        if not store.backup.verify(complete.name).ok:raise PilotStopped('Post-acquisition cache backup failed')
        with output.open('xb') as f:f.write(body);f.flush();os.fsync(f.fileno())
        output.chmod(0o400);sync_dir(output.parent)
        return descriptor


def assemble(store,baseline,transport,workspace):
    from sofascore_scraper.services.export import ExportService,DatasetSpec,DatasetFilter
    from sofascore_scraper.store import EventQuery,Scope
    import tempfile
    expected=check_inventory(baseline)
    ids=tuple(sorted(expected));active={i for i,e in expected.items() if e['current']}
    prior={(s['record']['event_id'],s['record']['key']):s['record'] for s in baseline['records']}
    records={};calendar={};files={};coverage=[]
    with tempfile.TemporaryDirectory(prefix='pilot-export-',dir=workspace) as tmp:
        tmp=Path(tmp)
        normalized=tmp/'events.jsonl'
        report=ExportService(store).export_dataset(DatasetSpec(dataset='events',schema='normalized',format='jsonl',filter=DatasetFilter(event_ids=ids)),normalized)
        if report.skipped:raise PilotStopped('Calendar export omitted evidence')
        calendar={row['id']:row for row in (json.loads(line) for line in normalized.read_bytes().splitlines())}
        raw=tmp/'raw.jsonl'
        report=store.export.raw(EventQuery(scope=Scope(event_ids=ids)),raw,keys=('event',*SECTIONS),fmt='jsonl')
        if report.skipped:raise PilotStopped('Raw export omitted evidence')
        for row in (json.loads(line) for line in raw.read_bytes().splitlines()):
            pair=row['event_id'],row['key']
            if row['event_id'] not in active:continue
            if row['event_id']!=PILOT:
                original=prior.get(pair)
                if original is None or original['payload']!=row['payload']:raise PilotStopped('Unqueried cache differs from its inventory')
                row=original # Keep exact original timestamp/spelling, never regenerate its freshness.
            records[pair]=row
    for ident in ids:
        if ident not in calendar:
            old=prior.get((ident,'listing'))
            if old is None:raise PilotStopped('Inventory event missing from cache/calendar')
            calendar[ident]=old['payload'] # Null observed_at remains null with exact provenance.
    if set(calendar)!=set(ids):raise PilotStopped('Unexpected calendar expansion')
    successful=[a for a in transport.state['attempts'] if a['status']==200]
    latest={a['path']:a for a in successful}
    files['provenance.jsonl']=b''.join(encode(dict(number=a['number'],path=a['path'],received_at=a['finished_at'],body_b64=base64.b64encode((transport.root/(transport.context['job_id']+'-responses')/(str(a['number'])+'.body')).read_bytes()).decode()))+b'\n' for a in successful)
    files['calendar.jsonl']=b''.join(encode(calendar[i])+b'\n' for i in ids)
    cal_lines={i:n for n,i in enumerate(ids,1)}
    refs={}
    for team in baseline['teams']:
        name=f'team_{team}.jsonl';rows=[]
        for pair,row in sorted(records.items()):
            e=expected[pair[0]]
            if team in (e['home'],e['away']):
                rows.append(row);refs.setdefault(pair,dict(file=name,line=len(rows)))
        files[name]=b''.join(encode(row)+b'\n' for row in rows)
    for ident in sorted(active):
        event=records.get((ident,'event'))
        from sofascore_scraper.status import classify_status
        from sofascore_scraper.services.planning import phase_of
        from sofascore_scraper.services.pipeline import body_state
        phase=phase_of(classify_status(event['payload'] if event else calendar[ident]).value)
        for key in ('calendar','event',*SECTIONS):
            if key=='calendar':
                at=(calendar[ident].get('quality') or {}).get('observed_at_utc')
                checked=latest[PATHS[1]]['finished_at'] if ident==PILOT else at
                coverage.append(dict(event_id=ident,resource=key,status='data',origin='attempt' if ident==PILOT else 'inventory',
                                     evidence=dict(file='calendar.jsonl',line=cal_lines[ident]),checked_at=checked))
                continue
            row=records.get((ident,key))
            if (key in SECTIONS and phase in ('pre','live')) or (key=='event' and row is None and phase=='pre'):
                coverage.append(dict(event_id=ident,resource=key,status='not_applicable',origin='phase',evidence=None,checked_at=None));continue
            if row is None or (key in SECTIONS and body_state(key,row['payload'])!='data'):raise PilotStopped('Resource coverage incomplete')
            coverage.append(dict(event_id=ident,resource=key,status='data',origin='attempt' if ident==PILOT else 'inventory',
                                 evidence=refs[ident,key],checked_at=latest[f'/event/{PILOT}'+('' if key=='event' else '/'+key)]['finished_at'] if ident==PILOT else row['fetched_at']))
    m=dict(schema=2,contract='pilot-v2',**{k:transport.context[k] for k in ('job_id','request_id','inventory_sha256')},
           recipe_id='sofascore-pilot-16599363-v1',provider_revision=REVISION,transport=transport.mode,
           generation_id=baseline['generation_id'],rules_sha256=producer_rules(),policy=dict(window_s=259200,interval_s=21600),
           started_at=transport.state['started_at'],finished_at=iso(transport.clock()),
           retained=sorted(i for i,e in expected.items() if not e['current']),attempts=transport.state['attempts'],errors=[],coverage=coverage,
           files=[dict(name=n,kind=('calendar' if n=='calendar.jsonl' else 'provenance') if not n.startswith('team_') else 'own_export',team_external_id=int(n[5:-6]) if n.startswith('team_') else None,bytes=len(raw),sha256=sha(raw)) for n,raw in sorted(files.items())])
    return encode(dict(manifest=m,files={n:base64.b64encode(raw).decode() for n,raw in files.items()}))
