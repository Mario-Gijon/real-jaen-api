"""Internal acquisition protocol. Only an explicitly installed test provider exists today."""
from contextlib import closing
from datetime import datetime, timezone
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import threading
import uuid
from .profile import Profile, atomic_json, sync_dir

PREFIX='/internal/v1/acquisitions'
RECIPES={'fixture-complete-v1','fixture-partial-v1','fixture-fail-v1','fixture-slow-v1'}
ACTIVE={'queued','running','cancelling'}
MAX_BUNDLE=2*1024*1024

def now(): return datetime.now(timezone.utc).isoformat()
def encoded(value): return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def secret(path):
    value=Path(path).read_bytes().strip()
    if not 32<=len(value)<=256 or not value.isascii() or any(c<=32 for c in value):
        raise ValueError('Invalid internal secret configuration')
    return value

class Conflict(Exception): pass

class Jobs:
    def __init__(self,root,provider):
        import fcntl
        self.root=Path(root);self.root.mkdir(exist_ok=True)
        self.lockfile=(self.root/'owner.lock').open('a+b')
        fcntl.flock(self.lockfile,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.provider=provider;self.guard=threading.RLock();self.stop=threading.Event()
        with closing(self.db()) as c,c:
            c.execute('CREATE TABLE IF NOT EXISTS job(id TEXT PRIMARY KEY,request TEXT UNIQUE,state TEXT,data TEXT)')
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active ON job((1)) WHERE state IN ('queued','running','cancelling')")
            for ident,data in c.execute("SELECT id,data FROM job WHERE state IN ('queued','running','cancelling')").fetchall():
                item=json.loads(data);item.update(state='interrupted',phase='interrupted',error='process_interrupted')
                c.execute('UPDATE job SET state=?,data=? WHERE id=?',('interrupted',json.dumps(item),ident))
        self.threads=[]
    def db(self):
        c=sqlite3.connect(self.root/'jobs.sqlite',timeout=10)
        c.execute('PRAGMA synchronous=FULL');return c
    def get(self,ident):
        with closing(self.db()) as c:
            row=c.execute('SELECT data FROM job WHERE id=?',(ident,)).fetchone()
        if not row: raise KeyError(ident)
        return json.loads(row[0])
    def save(self,item):
        with closing(self.db()) as c,c:
            c.execute('UPDATE job SET state=?,data=? WHERE id=?',(item['state'],json.dumps(item),item['id']))
    def submit(self,request,recipe):
        with self.guard,closing(self.db()) as c,c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT data FROM job WHERE request=?',(request,)).fetchone()
            if row:
                item=json.loads(row[0])
                if item['recipe_id']!=recipe: raise Conflict()
                return item,False
            if c.execute("SELECT 1 FROM job WHERE state IN ('queued','running','cancelling')").fetchone(): raise Conflict()
            item=dict(id=str(uuid.uuid4()),request_id=request,recipe_id=recipe,state='queued',phase='queued',progress=0,error=None,started_at=now())
            c.execute('INSERT INTO job VALUES(?,?,?,?)',(item['id'],request,item['state'],json.dumps(item)))
        worker=threading.Thread(target=self.run,args=(item['id'],),daemon=True)
        self.threads.append(worker);worker.start()
        return item,True
    def cancel(self,ident):
        with self.guard:
            item=self.get(ident)
            if item['state'] in ACTIVE:
                item.update(state='cancelling',phase='cancelling');self.save(item)
            return item
    def checkpoint(self,ident,progress):
        with self.guard:
            item=self.get(ident)
            if self.stop.is_set(): raise InterruptedError()
            if item['state']=='cancelling': raise InterruptedError('cancelled')
            item.update(state='running',phase='collecting',progress=progress);self.save(item)
    def run(self,ident):
        try:
            self.checkpoint(ident,1)
            item=self.get(ident)
            payload=self.provider.collect(item['recipe_id'],lambda value:self.checkpoint(ident,value))
            with self.guard:
                self.checkpoint(ident,90);item=self.get(ident)
                raw=payload['raw'];assert len(raw)<=512*1024
                manifest=dict(schema=1,job_id=ident,request_id=item['request_id'],recipe_id=item['recipe_id'],
                    provider='simulated-local-v1',provider_revision=self.provider.revision,is_simulated=True,
                    scope='fixture-real-jaen-v1',started_at=item['started_at'],finished_at=now(),
                    coverage=payload['coverage'],errors=payload['errors'],
                    files=[dict(name='raw.jsonl',kind='export',team_external_id=2841,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())])
                bundle=encoded(dict(manifest=manifest,files={'raw.jsonl':base64.b64encode(raw).decode()}))
                if len(bundle)>MAX_BUNDLE: raise ValueError('Bundle too large')
                folder=self.root/'bundles';folder.mkdir(exist_ok=True)
                path=folder/(ident+'.json');temporary=folder/(ident+'.tmp')
                with temporary.open('xb') as out:out.write(bundle);out.flush();os.fsync(out.fileno())
                temporary.chmod(0o400);os.replace(temporary,path);sync_dir(folder);sync_dir(self.root)
                item.update(state='completed',phase='finished',progress=100,bundle={'sha256':hashlib.sha256(bundle).hexdigest(),'bytes':len(bundle)})
                self.save(item)
        except Exception as error:
            with self.guard:
                item=self.get(ident)
                state='cancelled' if item['state']=='cancelling' else ('interrupted' if isinstance(error,InterruptedError) else 'failed')
                item.update(state=state,phase='finished',error=None if state=='cancelled' else 'provider_interrupted' if state=='interrupted' else 'provider_failed')
                self.save(item)
    def bundle(self,ident):
        item=self.get(ident)
        if item['state']!='completed':raise Conflict()
        body=(self.root/'bundles'/(ident+'.json')).read_bytes()
        if len(body)!=item['bundle']['bytes'] or hashlib.sha256(body).hexdigest()!=item['bundle']['sha256']:raise RuntimeError('Bundle integrity failed')
        return body,item['bundle']['sha256']
    def close(self):
        self.stop.set()
        for thread in self.threads:thread.join(3)
        self.lockfile.close()

class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,jobs,token):
        self.jobs,self.token=jobs,token
        super().__init__(address,Handler)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,status,value):
        data=encoded(value);self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def handle_request(self,post):
        try:
            if not post and self.path=='/health':return self.reply(200,{'status':'ok'})
            provided=self.headers.get('Authorization','').encode('utf-8')
            if not hmac.compare_digest(provided,b'Bearer '+self.server.token):return self.reply(401,{'error':'unauthorized'})
            if self.path==PREFIX and post:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=2048:raise ValueError()
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict) or set(data)!={'request_id','recipe_id'} or not all(isinstance(data[k],str) for k in data) or data['recipe_id'] not in RECIPES or str(uuid.UUID(data['request_id']))!=data['request_id']:raise ValueError()
                item,new=self.server.jobs.submit(data['request_id'],data['recipe_id']);return self.reply(202 if new else 200,item)
            match=re.fullmatch(PREFIX+r'/([0-9a-f-]{36})(/cancel|/bundle)?',self.path)
            if not match or str(uuid.UUID(match[1]))!=match[1]:return self.reply(404,{'error':'not_found'})
            ident,action=match.groups()
            if post and action=='/cancel':return self.reply(200,self.server.jobs.cancel(ident))
            if not post and action=='/bundle':
                body,digest=self.server.jobs.bundle(ident)
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('ETag','\"'+digest+'\"');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
            if not post and action is None:return self.reply(200,self.server.jobs.get(ident))
            return self.reply(404,{'error':'not_found'})
        except Conflict:return self.reply(409,{'error':'conflict'})
        except KeyError:return self.reply(404,{'error':'not_found'})
        except (ValueError,TypeError):return self.reply(400,{'error':'invalid_request'})
        except Exception:return self.reply(503,{'error':'unavailable'})
    def do_GET(self):self.handle_request(False)
    def do_POST(self):self.handle_request(True)

def main():
    if os.environ.get('ACQ_TEST_MODE')!='1' or os.environ.get('ACQ_PROVIDER')!='simulated-local-v1':
        raise SystemExit('No production acquisition provider is configured; explicit isolated test mode required')
    provider=importlib.import_module('acquisition_simulated').Provider()
    token=secret(os.environ['ACQ_SECRET_FILE'])
    with Profile(os.environ.get('ACQ_PROFILE_ROOT','/profiles')) as owner:
        owner.verify()
        jobs=Jobs(os.environ.get('ACQ_JOBS_ROOT','/acquisition-jobs'),provider)
        server=Server(('0.0.0.0',8002),jobs,token)
        def shutdown(*args):threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGTERM,shutdown)
        try:server.serve_forever(poll_interval=.1)
        finally:server.server_close();jobs.close()

if __name__=='__main__':main()
