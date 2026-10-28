"""Fixed-route pilot HTTP transport. Real egress disabled unless explicitly enabled by its caller."""
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from .profile import atomic_json
from persistencia.bundle_v2 import PATHS, encode, fingerprint, parse


def iso(at):return datetime.fromtimestamp(at,timezone.utc).isoformat()

class PilotStopped(RuntimeError):pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None

class PilotTransport:
    def __init__(self,root,*,job_id,request_id,inventory_sha256,loopback_url=None,enable_real=False,
                 max_attempts=16,seconds=300,clock=time.time,sleep=asyncio.sleep,cancelled=lambda:False,breaker=None):
        import uuid
        if any(str(uuid.UUID(v))!=v for v in (job_id,request_id)):raise ValueError('Invalid job context')
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/(job_id+'.json');self.retry_path=self.root/'retry.json'
        self.context=dict(job_id=job_id,request_id=request_id,inventory_sha256=inventory_sha256)
        self.mode='loopback-fixture' if loopback_url is not None else 'sofascore-https'
        if loopback_url is not None:
            p=urlsplit(loopback_url)
            if p.scheme!='http' or p.hostname!='127.0.0.1' or not p.port or p.path!='/api/v1' or p.username or p.password or p.query or p.fragment:raise ValueError('Only a literal loopback fixture server is allowed')
            self.base=loopback_url
        elif enable_real:self.base='https://www.sofascore.com/api/v1'
        else:raise PilotStopped('Proveedor real deshabilitado')
        if type(max_attempts) is not int or not 1<=max_attempts<=16 or type(seconds) is not int or not 1<=seconds<=300:raise ValueError('Unapproved pilot limits')
        self.max_attempts=max_attempts;self.seconds=seconds;self.clock=clock;self.sleep=sleep;self.cancelled=cancelled
        self.state=parse(self.path.read_bytes()) if self.path.exists() else dict(context=self.context,started_at=iso(clock()),attempts=[])
        if self.state['context']!=self.context:raise PilotStopped('Job identity reused with another inventory')
        self.retry=parse(self.retry_path.read_bytes()) if self.retry_path.exists() else {}
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        self.memo={};self.owner=None;self.serial=None
        if breaker is None:
            from sofascore_scraper.breaker import CircuitBreaker
            breaker=CircuitBreaker(consecutive=20,ratio=.9,server_errors=3,bridge_blocked=lambda *a:False)
        self.breaker=breaker

    def __enter__(self):
        import fcntl
        self.owner=(self.root/'owner.lock').open('a+b')
        try:
            fcntl.flock(self.owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if self.path.exists():self.state=parse(self.path.read_bytes())
            if self.state['context']!=self.context:raise PilotStopped('Job identity changed')
            self.retry=parse(self.retry_path.read_bytes()) if self.retry_path.exists() else {}
            for a in self.state['attempts']:
                if a['status'] is None:
                    self.retry[a['path']]=dict(reason='interrupted',next_at=self.clock()+30,admin=False)
                    a['status']=0;a['finished_at']=iso(self.clock())
            atomic_json(self.path,self.state);atomic_json(self.retry_path,self.retry)
            return self
        except BaseException:
            self.owner.close();self.owner=None;raise
    def __exit__(self,*exc):
        if self.owner:self.owner.close();self.owner=None
    async def __aenter__(self):
        if self.owner is None:raise PilotStopped('Exclusive pilot ownership required')
        self.serial=asyncio.Lock();return self
    async def __aexit__(self,*exc):pass

    def _check(self):
        if self.owner is None:raise PilotStopped('No owner')
        if self.cancelled():raise InterruptedError('Pilot cancelled')
        if self.clock()>=datetime.fromisoformat(self.state['started_at']).timestamp()+self.seconds:raise PilotStopped('Pilot time limit')
        if self.breaker.tripped:raise PilotStopped('Circuit breaker open')
        if self.retry.get('_blocked',{}).get('admin'):raise PilotStopped('Operator review required after refusal')
        if self.retry.get('_blocked',{}).get('next_at',0)>self.clock():raise PilotStopped('Pilot stopped after refusal/breaker')

    async def _wait_until(self,at):
        deadline=datetime.fromisoformat(self.state['started_at']).timestamp()+self.seconds
        if at>=deadline:raise PilotStopped('Retry does not fit job deadline')
        while self.clock()<at:
            self._check();await self.sleep(min(.25,at-self.clock()))

    def _request(self,path):
        req=urllib.request.Request(self.base+path,headers={'Accept':'application/json','User-Agent':'rj-analyst-pilot/1'})
        try:response=self.opener.open(req,timeout=10)
        except urllib.error.HTTPError as e:response=e
        with response:
            raw=response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:raise ValueError('Response too large')
            return response.status,dict(response.headers),raw

    @staticmethod
    def retry_after(headers,now):
        value=next((v for k,v in headers.items() if k.lower()=='retry-after'),None)
        if value is None:return 0
        try:return max(0,float(value))
        except ValueError:
            try:return max(0,parsedate_to_datetime(value).timestamp()-now)
            except (ValueError,TypeError,OverflowError):return 0

    async def get(self,path,retries=None):
        from sofascore_scraper.slices import Outcome
        if path not in PATHS:raise PilotStopped('Path not in fixed pilot allowlist')
        async with self.serial:
            self._check()
            if path in self.memo:return self.memo[path]
            while True:
                self._check()
                prior=self.retry.get(path,{})
                if prior.get('admin'):raise PilotStopped('Resource requires administrative review')
                await self._wait_until(prior.get('next_at',0))
                attempts=[a for a in self.state['attempts'] if a['path']==path]
                if attempts and attempts[-1]['status']==200 and path not in self.retry:raise PilotStopped('Resource already acquired: use its verified cache/bundle')
                if len(attempts)>=2 or len(self.state['attempts'])>=self.max_attempts:raise PilotStopped('Pilot attempt budget exhausted')
                await self._wait_until(self.retry.get('_last_sent',{}).get('at',0)+1)
                self._check()
                a=dict(number=len(self.state['attempts'])+1,path=path,sent_at=iso(self.clock()),finished_at=None,status=None,payload_sha256=None)
                self.state['attempts'].append(a)
                atomic_json(self.path,self.state)  # Durable charge BEFORE the socket can be opened.
                self.retry['_last_sent']={'at':self.clock()}
                atomic_json(self.retry_path,self.retry)
                status=0;headers={};payload=None;reason=None;raw=None
                try:
                    status,headers,raw=await asyncio.to_thread(self._request,path)
                    folder=self.root/(self.context['job_id']+'-responses');folder.mkdir(exist_ok=True)
                    try:
                        with (folder/(str(a['number'])+'.body')).open('xb') as evidence:
                            evidence.write(raw);evidence.flush();os.fsync(evidence.fileno())
                    except OSError as error:raise PilotStopped('Cannot preserve response evidence; stop acquisition') from error
                    if status==200:
                        try:
                            payload=parse(raw)
                            if not isinstance(payload,(dict,list)):raise ValueError('Bad JSON shape')
                        except (ValueError,UnicodeError):reason='parse'
                    elif status in (403,429):reason=str(status)
                    elif status==404:reason='404'
                    elif status>=500:reason='5xx'
                    else:reason='redirect' if 300<=status<400 else 'http'
                except (OSError,urllib.error.URLError):reason='network'
                except ValueError:reason='parse'
                now=self.clock();a.update(finished_at=iso(now),status=status,
                                         payload_sha256=fingerprint(payload.get('event',payload) if isinstance(payload,dict) else payload) if payload is not None else None)
                from sofascore_scraper.services.pipeline import body_state
                key=path.rsplit('/',1)[-1]
                if reason is None and key in ('lineups','statistics','incidents') and body_state(key,payload)!='data':reason='parse' if body_state(key,payload)=='malformed' else 'empty'
                if reason is None:
                    self.breaker.record('ok');self.retry.pop(path,None)
                    atomic_json(self.path,self.state);atomic_json(self.retry_path,self.retry)
                    answer=Outcome('ok',data=payload,http_status=status,fetched_at=datetime.fromisoformat(a['finished_at']),via='curl')
                    self.memo[path]=answer;return answer
                self.breaker.record(reason if reason in ('403','429','5xx','network','parse') else 'other')
                previous=self.retry.get(path,{}).get('failures',0)+1
                base=900 if reason in ('429','parse','empty','404','redirect','http') else min(900,30*2**min(previous-1,5))
                wait=max(base,self.retry_after(headers,now))
                self.retry[path]=dict(reason=reason,next_at=now+wait,admin=reason=='403',failures=previous)
                if reason in ('403','429') or self.breaker.tripped:
                    self.retry['_blocked']=dict(next_at=now+max(900,wait),admin=reason=='403')
                atomic_json(self.path,self.state);atomic_json(self.retry_path,self.retry)
                if reason in ('403','429') or self.breaker.tripped:raise PilotStopped('Refusal/breaker: stop entire pilot')
                if reason in ('parse','empty','404','redirect','http') or len(attempts)+1>=2:
                    return Outcome('empty' if reason in ('empty','404') else 'failed',data=payload if reason=='empty' else None,
                                   reason=reason,http_status=status or None,fetched_at=datetime.fromisoformat(a['finished_at']))
                # Retry only a transient network/5xx error, after its durable due time.
