"""Explicit private pilot receiver. Disabled by default; no scraper or browser dependencies."""
import hashlib
import re
import threading
import urllib.request
import uuid
from pathlib import Path
from .adquisicion import Cliente, AdquisicionRechazada, NoRedirect
from .bundle_v2 import MAX_WIRE, Rejected, inventory, verify

class Receptor:
    def __init__(self,*,enable_real=False,enable_test=False,expected_rules=None):
        self.enable_real,self.enable_test,self.expected_rules=enable_real,enable_test,expected_rules
        self.guard=threading.Lock()

    def receive(self,coordinator,body,descriptor,baseline,*,context):
        marker=coordinator.base.with_name(coordinator.base.name+'.pilot-test-installation')
        if self.enable_test:
            if not marker.is_file() or marker.read_text()!='isolated-pilot-v2\n':raise AdquisicionRechazada('Instalacion de prueba no identificada')
        elif not self.enable_real or marker.exists():raise AdquisicionRechazada('Proveedor real deshabilitado')
        with self.guard:
            # Copy and fsync the bounded envelope before validation/import. Never trust a remote path.
            job,request=context
            if any(str(uuid.UUID(v))!=v for v in context) or not re.fullmatch('[0-9a-f]{64}',descriptor.get('sha256','')):raise Rejected('Invalid identity')
            if len(body)>MAX_WIRE:raise Rejected('Bundle too large')
            folder=coordinator.directorio/'acquisition-v2'/job/descriptor['sha256']
            folder.mkdir(parents=True,exist_ok=True)
            if folder.is_symlink():raise Rejected('Unsafe staging')
            path=folder/'bundle.json';Cliente._immutable(path,body)
            manifest,files=verify(path.read_bytes(),descriptor,baseline,context=context,expected_rules=self.expected_rules,test_transport=self.enable_test)
            ident=str(uuid.uuid5(uuid.NAMESPACE_URL,'urn:rj:pilot-v2:'+job+':'+descriptor['sha256']))
            try:previous=coordinator.estado(ident)
            except KeyError:previous=None
            if previous is None:
                if inventory(coordinator.capturar())['sha256']!=baseline['sha256']:raise Rejected('La generacion cambio durante la adquisicion')
                exports=[]
                for name,raw in files.items():
                    out=folder/name;Cliente._immutable(out,raw)
                    if name.startswith('team_'):exports.append((out,int(name[5:-6])))
                coordinator.solicitar(exportaciones=exports,calendarios=[folder/'calendar.jsonl'],identificador=ident,generacion_esperada=baseline['generation_id'])
            result=coordinator.esperar(ident,timeout=120)
            if result['estado']!='completed':raise AdquisicionRechazada('Publicacion fallida: se conserva la generacion valida')
            return dict(state='published',generation_id=result['generacion'],job_id=ident,acquisition_id=job)

    def download(self,url,secret_file,*,job_id,descriptor):
        """Fixed internal bundle route only. No redirects/proxies; authorization never logged."""
        from urllib.parse import urlsplit
        p=urlsplit(url)
        if p.scheme!='http' or not p.hostname or p.username or p.password or p.path not in ('','/') or p.query or p.fragment:raise Rejected('Invalid internal service URL')
        if str(uuid.UUID(job_id))!=job_id:raise Rejected('Invalid job')
        if not (self.enable_real or self.enable_test):raise Rejected('Proveedor real deshabilitado')
        if p.hostname not in (('127.0.0.1',) if self.enable_test else ('acquisition',)):raise Rejected('Unapproved internal host')
        client=Cliente(url,secret_file)
        request=urllib.request.Request(url.rstrip('/')+'/internal/v1/acquisitions/'+job_id+'/bundle',
                    headers={'Authorization':'Bearer '+client._secret.decode('ascii')})
        with client.opener.open(request,timeout=10) as response:
            if response.headers.get('ETag')!='"'+descriptor['sha256']+'"':raise Rejected('Bundle ETag mismatch')
            body=response.read(MAX_WIRE+1)
        if len(body)>MAX_WIRE or len(body)!=descriptor['bytes'] or hashlib.sha256(body).hexdigest()!=descriptor['sha256']:raise Rejected('Bundle download mismatch')
        return body
