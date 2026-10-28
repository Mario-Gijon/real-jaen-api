"""Split-container acceptance on a named, isolated test SQLite; no SDK/browser dependency."""
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import threading
import urllib.error
import uuid

from persistencia import sqlite,sincronizacion,lectura
from persistencia.actualizacion import Coordinador
from persistencia.adquisicion_v2 import Receptor
from persistencia.bundle_v2 import encode,parse,sha,inventory,Rejected
from persistencia.bloqueo import bloquear

BASE=Path('/state/pilot.sqlite');EXCHANGE=Path('/imports');AMBITO=Path('/app/ambito.json')

def identity():
    with bloquear(BASE),closing(sqlite._lectura(BASE)) as c:
        return {t:c.execute(f'SELECT '+cols+f' FROM {t} ORDER BY id').fetchall() for t,cols in
                [('team','id,uid'),('player','id,uid'),('match','id,uid'),('observation','id,content_hash'),
                 ('local_replacement','id,replaced_match_id,current_match_id,status')]}

def prepare():
    assert importlib.util.find_spec('sofascore_scraper') is None
    assert importlib.util.find_spec('patchright') is None
    sqlite.copia_consistente('/state/input.sqlite',BASE)
    checked=sincronizacion.verificar(BASE);assert checked['ok'],checked
    # Explicit maintenance on this disposable copy only; do not weaken the coordinator's archived-code guard.
    rebuilt=sincronizacion.reconstruir(BASE,confirmar=True,comando='offline pilot test copy: explicit rule adoption')
    checked=sincronizacion.verificar(BASE);assert checked['ok'],checked
    BASE.with_name(BASE.name+'.pilot-test-installation').write_text('isolated-pilot-v2\n')
    with Coordinador(BASE,AMBITO,reserva=0) as c:
        baseline=inventory(c.capturar())
        (EXCHANGE/'inventory.json').write_bytes(encode(baseline))
    ids=identity()
    (EXCHANGE/'ids-before.json').write_bytes(encode(ids))
    events=baseline['events']
    report=dict(stage='backend_prepare',revision=sqlite.describir(BASE)['revision'],inventory_sha256=baseline['sha256'],
                retained=len(events),current=sum(e['current'] for e in events),
                historical=[e['id'] for e in events if not e['current']],
                calendar_only=[s['record']['event_id'] for s in baseline['records'] if s['record']['key']=='listing'])
    (EXCHANGE/'prepare-report.json').write_bytes(encode(report));print(encode(report).decode())

def receive(expected_rules):
    assert importlib.util.find_spec('sofascore_scraper') is None
    assert importlib.util.find_spec('patchright') is None
    baseline=parse((EXCHANGE/'inventory.json').read_bytes())
    first=parse((EXCHANGE/'bundle-meta.json').read_bytes());second=parse((EXCHANGE/'second-meta.json').read_bytes())
    assert first['rules_sha256']==expected_rules==second['rules_sha256']
    before=sqlite.describir(BASE);ids_before=parse((EXCHANGE/'ids-before.json').read_bytes())
    received=(EXCHANGE/'bundle.json').read_bytes()
    token=secrets.token_hex(32);secret_file=Path('/state/token-test');secret_file.write_text(token);secret_file.chmod(0o600)
    job=first['context'][0];path='/internal/v1/acquisitions/'+job+'/bundle'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.headers.get('Authorization')!='Bearer '+token:
                self.send_response(401);self.send_header('Content-Length','0');self.end_headers();return
            if self.path!=path:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Length',str(len(received)));self.send_header('ETag','"'+first['descriptor']['sha256']+'"');self.end_headers();self.wfile.write(received)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    receiver=Receptor(enable_test=True,expected_rules=expected_rules)
    try:
        url=f'http://127.0.0.1:{server.server_port}'
        bad=Path('/state/bad-token');bad.write_text('x'*64)
        try:receiver.download(url,bad,job_id=job,descriptor=first['descriptor']);raise AssertionError('Wrong secret accepted')
        except urllib.error.HTTPError as error:assert error.code==401;error.close()
        body=receiver.download(url,secret_file,job_id=job,descriptor=first['descriptor'])
        assert body==received
    finally:server.shutdown();server.server_close();thread.join();secret_file.unlink();bad.unlink()
    with Coordinador(BASE,AMBITO,reserva=0) as c:
        old=c.capturar()
        assert old.id==baseline['generation_id']
        try:Receptor(expected_rules=expected_rules).receive(c,body,first['descriptor'],baseline,context=first['context']);raise AssertionError('Default enabled')
        except Exception as e:assert 'deshabilitado' in str(e)
        # Failures at the trust boundary never submit a candidate.
        for mutation in ('coverage','history','inventory','payload'):
            altered=parse(body)
            if mutation=='coverage':altered['manifest']['coverage'][0]['status']='pending'
            elif mutation=='history':altered['manifest']['retained']=[]
            elif mutation=='inventory':altered['manifest']['inventory_sha256']='0'*64
            else:altered['files'][next(n for n in altered['files'] if n!='calendar.jsonl')]='e30K'
            raw=encode(altered);desc=dict(bytes=len(raw),sha256=sha(raw))
            try:receiver.receive(c,raw,desc,baseline,context=first['context']);raise AssertionError('Tamper accepted')
            except Rejected:pass
            assert c.capturar().id==old.id
        visited=[]
        def fault(point):
            visited.append(point)
            if point=='candidate_committed':raise RuntimeError('Injected failure after candidate commit')
        c._frontera=fault
        try:receiver.receive(c,body,first['descriptor'],baseline,context=first['context']);raise AssertionError('Fault published')
        except Exception as error:assert 'fallida' in str(error)
        assert c.capturar() is old
        assert 'candidate_committed' in visited,visited
        c._frontera=lambda point:None
        body2=(EXCHANGE/'bundle-second.json').read_bytes()
        try:result=receiver.receive(c,body2,second['descriptor'],baseline,context=second['context'])
        except Exception:
            ident=str(uuid.uuid5(uuid.NAMESPACE_URL,'urn:rj:pilot-v2:'+second['context'][0]+':'+second['descriptor']['sha256']))
            print('PUBLICATION_DIAGNOSTIC='+encode(c.estado(ident)).decode(),flush=True)
            raise
        assert result['generation_id']!=old.id
        assert receiver.receive(c,body2,second['descriptor'],baseline,context=second['context'])==result
        # A new job based on an older inventory cannot publish over the new generation.
        altered=parse(body2);altered['manifest']['job_id']=str(uuid.uuid4());altered['manifest']['request_id']=str(uuid.uuid4())
        context=[altered['manifest']['job_id'],altered['manifest']['request_id']];raw=encode(altered)
        try:receiver.receive(c,raw,dict(bytes=len(raw),sha256=sha(raw)),baseline,context=context);raise AssertionError('Stale generation accepted')
        except Rejected as e:assert 'generacion' in str(e)
        assert c.capturar().id==result['generation_id']
        published=c.capturar()
        assert old.consultar('/api/v1/matches/16599363')[0]==200
        assert old.id!=published.id
        _,scope=published.consultar('/api/v1/scope')
    assert before!=sqlite.describir(BASE)
    ids_after=identity()
    for table in ('team','player','match','local_replacement'):
        assert ids_before[table]==[list(row) for row in ids_after[table]],table
    assert {tuple(r) for r in ids_before['observation']} <= set(ids_after['observation'])
    # Every response from the published snapshot equals the independent existing SQL/API reader.
    independent=lectura.cargar_desde_base(BASE,AMBITO)
    import api
    from test_carga import rutas
    count=0
    for route in rutas(independent):
        assert published.consultar(route)==api.consultar(independent,route),route
        count+=1
    with Coordinador(BASE,AMBITO,reserva=0) as reopened:
        assert reopened.capturar().id==published.id
        assert inventory(reopened.capturar())['events']==baseline['events']
    checked=sincronizacion.verificar(BASE);assert checked['ok'],checked
    report=dict(stage='backend_receive',publication='completed',routes_equivalent=count,
                ids_preserved=True,original_observations_preserved=True,local_decisions_preserved=True,
                tampered_bundles_rejected=4,default_disabled=True,authentication_checked=True,
                post_commit_candidate_failure='principal and old snapshot preserved',
                restart_generation_preserved=True,duplicate_publication_idempotent=True,stale_generation_rejected=True,
                sdk_installed=False,browser_installed=False,integrity='ok',base='exclusive temporary pilot installation')
    (EXCHANGE/'receive-report.json').write_bytes(encode(report));print(encode(report).decode())

def verify_real(expected_rules):
    """Real single run: trust-boundary verification only (inventory, coverage, generation, provenance), no candidate."""
    import base64
    from persistencia.bundle_v2 import verify,fingerprint,PILOT
    assert importlib.util.find_spec('sofascore_scraper') is None
    baseline=parse((EXCHANGE/'inventory.json').read_bytes());meta=parse((EXCHANGE/'bundle-meta.json').read_bytes())
    assert meta['rules_sha256']==expected_rules
    body=(EXCHANGE/'bundle.json').read_bytes()
    verify(body,meta['descriptor'],baseline,context=meta['context'],expected_rules=expected_rules)
    try:verify(body,meta['descriptor'],baseline,context=meta['context'],expected_rules=expected_rules,test_transport=True);raise AssertionError('Real bundle accepted as fixture')
    except Rejected:pass
    m=parse(body)['manifest'];files=parse(body)['files']
    prior={s['record']['key']:s['record']['payload'] for s in baseline['records'] if s['record']['event_id']==PILOT}
    fresh={}
    for name,raw in files.items():
        if name.startswith('team_'):
            for line in base64.b64decode(raw).splitlines():
                row=parse(line)
                if row['event_id']==PILOT:fresh[row['key']]=row['payload']
    changes={k:('same' if k in prior and fingerprint(prior[k])==fingerprint(v) else 'changed' if k in prior else 'new') for k,v in sorted(fresh.items())}
    statuses={}
    for c in m['coverage']:statuses[c['status']]=statuses.get(c['status'],0)+1
    report=dict(stage='backend_verify_real',accepted=True,transport=m['transport'],generation_id=m['generation_id'],
                inventory_sha256=m['inventory_sha256'],attempts=[(a['path'],a['status']) for a in m['attempts']],
                coverage=statuses,retained=len(m['retained']),pilot_resources_vs_inventory=changes,
                publication=None,base='inactive snapshot copy in its own volume')
    (EXCHANGE/'verify-report.json').write_bytes(encode(report));print(encode(report).decode())

if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare()
    elif sys.argv[1]=='verify-real':verify_real(sys.argv[2])
    else:receive(sys.argv[2])
