"""Private acquisition client: copy/verify a sealed bundle before the existing coordinator."""
import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

LIMIT=2*1024*1024
RECIPES={'fixture-complete-v1','fixture-partial-v1','fixture-fail-v1','fixture-slow-v1'}
class AdquisicionRechazada(RuntimeError):pass

def strict_object(pairs):
    value={}
    for k,v in pairs:
        if k in value:raise ValueError('Duplicate key')
        value[k]=v
    return value

def validar_bundle(body,descriptor,*,job_id,request_id,recipe_id):
    try:
        if set(descriptor)!={'bytes','sha256'} or type(descriptor['bytes']) is not int or len(body)!=descriptor['bytes'] or len(body)>LIMIT or hashlib.sha256(body).hexdigest()!=descriptor['sha256']:raise ValueError()
        bundle=json.loads(body,object_pairs_hook=strict_object,parse_constant=lambda v:(_ for _ in ()).throw(ValueError()))
        if set(bundle)!={'manifest','files'}:raise ValueError()
        m=bundle['manifest']
        expected={'schema','job_id','request_id','recipe_id','provider','provider_revision','is_simulated','scope','started_at','finished_at','coverage','errors','files'}
        if set(m)!=expected or type(m['schema']) is not int or m['schema']!=1:raise ValueError()
        if (m['job_id'],m['request_id'],m['recipe_id'])!=(job_id,request_id,recipe_id):raise ValueError()
        if m['provider']!='simulated-local-v1' or m['is_simulated'] is not True or m['scope']!='fixture-real-jaen-v1':raise ValueError()
        if not re.fullmatch('[0-9a-f]{64}',m['provider_revision']):raise ValueError()
        if m['coverage']!='complete' or m['errors']!=[]:raise ValueError()
        start,end=[datetime.fromisoformat(m[k]) for k in ('started_at','finished_at')]
        if not start.tzinfo or not end.tzinfo or end<start:raise ValueError()
        if set(bundle['files'])!={'raw.jsonl'} or len(m['files'])!=1:raise ValueError()
        f=m['files'][0]
        if set(f)!={'name','kind','team_external_id','bytes','sha256'} or f['name']!='raw.jsonl' or f['kind']!='export' or type(f['team_external_id']) is not int or f['team_external_id']!=2841:raise ValueError()
        raw=base64.b64decode(bundle['files']['raw.jsonl'],validate=True)
        if type(f['bytes']) is not int or not 0<len(raw)<=512*1024 or f['bytes']!=len(raw) or hashlib.sha256(raw).hexdigest()!=f['sha256']:raise ValueError()
        return raw,m
    except Exception:
        raise AdquisicionRechazada('Bundle incompleto, manipulado o incompatible') from None

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None

class Cliente:
    def __init__(self,url,secret_file,*,allow_simulated=False):
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme!='http' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
            raise AdquisicionRechazada('Configuracion interna invalida')
        self.url=url.rstrip('/')+'/internal/v1/acquisitions'
        self._secret=Path(secret_file).read_bytes().strip()
        if not 32<=len(self._secret)<=256 or not self._secret.isascii() or any(c<=32 for c in self._secret):raise AdquisicionRechazada('Secreto interno no configurado')
        self.allow_simulated=allow_simulated
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        self.guard=threading.Lock()
    def _http(self,method,suffix='',data=None,*,bundle=False):
        if not self.allow_simulated:raise AdquisicionRechazada('Simulacion deshabilitada')
        body=json.dumps(data).encode() if data is not None else (b'' if method=='POST' else None)
        request=urllib.request.Request(self.url+suffix,data=body,method=method,headers={'Authorization':'Bearer '+self._secret.decode('ascii'),'Content-Type':'application/json'})
        try:
            with self.opener.open(request,timeout=5) as response:
                raw=response.read(LIMIT+1)
                if len(raw)>LIMIT:raise AdquisicionRechazada('Respuesta interna demasiado grande')
                if bundle:
                    etag=response.headers.get('ETag','')
                    return raw,etag[1:-1] if len(etag)==66 and etag.startswith(chr(34)) and etag.endswith(chr(34)) else None
                return json.loads(raw)
        except urllib.error.HTTPError as error:raise AdquisicionRechazada('Respuesta interna HTTP '+str(error.code)) from None
        except (OSError,ValueError):raise AdquisicionRechazada('Adquisicion no disponible') from None
    def solicitar(self,request_id,recipe_id):
        if str(uuid.UUID(request_id))!=request_id or recipe_id not in RECIPES:raise AdquisicionRechazada('Receta o solicitud invalida')
        return self._http('POST',data={'request_id':request_id,'recipe_id':recipe_id})
    def estado(self,ident):
        if str(uuid.UUID(ident))!=ident:raise AdquisicionRechazada('ID invalido')
        return self._http('GET','/'+ident)
    def cancelar(self,ident):
        if str(uuid.UUID(ident))!=ident:raise AdquisicionRechazada('ID invalido')
        return self._http('POST','/'+ident+'/cancel')
    def esperar(self,ident,timeout=20):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            state=self.estado(ident)
            if state['state'] not in ('queued','running','cancelling'):return state
            time.sleep(.05)
        raise AdquisicionRechazada('Adquisicion sigue en curso')
    def publicar(self,coordinator,request_id,recipe_id):
        marker=coordinator.base.with_name(coordinator.base.name+'.simulated-installation')
        if not self.allow_simulated or not marker.is_file() or marker.read_text().strip()!='isolated-fixtures-v1':
            raise AdquisicionRechazada('Simulacion deshabilitada en esta instalacion')
        with self.guard:
            job=self.solicitar(request_id,recipe_id);state=self.esperar(job['id'])
            if state['state']!='completed':raise AdquisicionRechazada('Adquisicion no terminada correctamente')
            body,etag=self._http('GET','/'+job['id']+'/bundle',bundle=True)
            descriptor=state['bundle']
            if etag!=descriptor['sha256']:raise AdquisicionRechazada('Descriptor de bundle incoherente')
            folder=coordinator.directorio/'acquisition-bundles'/job['id']/descriptor['sha256']
            # Never accept a server-supplied path or unvalidated digest as a filesystem name.
            if str(uuid.UUID(job['id']))!=job['id'] or not re.fullmatch('[0-9a-f]{64}',descriptor['sha256']):raise AdquisicionRechazada('ID de bundle invalido')
            folder.mkdir(parents=True,exist_ok=True)
            if folder.is_symlink():raise AdquisicionRechazada('Staging inseguro')
            path=folder/'bundle.json'
            self._immutable(path,body)
            raw,manifest=validar_bundle(path.read_bytes(),descriptor,job_id=job['id'],request_id=request_id,recipe_id=recipe_id)
            export=folder/'exportacion.jsonl';self._immutable(export,raw)
            ident=str(uuid.uuid5(uuid.NAMESPACE_URL,'urn:rj:acquisition:'+job['id']+':'+descriptor['sha256']))
            task=coordinator.solicitar(exportaciones=[(export,2841)],identificador=ident)
            published=coordinator.esperar(task)
            if published['estado']!='completed':raise AdquisicionRechazada('Importacion o publicacion fallida')
            return {'acquisition_id':job['id'],'job_id':task,'generation_id':published['generacion'],'state':'published'}
    @staticmethod
    def _immutable(path,body):
        if path.is_symlink():raise AdquisicionRechazada('Staging inseguro')
        if path.exists():
            if path.read_bytes()!=body:raise AdquisicionRechazada('Copia privada modificada')
            return
        temporary=path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
        with temporary.open('xb') as out:out.write(body);out.flush();os.fsync(out.fileno())
        os.replace(temporary,path)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
