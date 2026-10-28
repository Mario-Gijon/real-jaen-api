"""Offline split-container pilot. Existing images only; consistent non-active snapshot required."""
import argparse
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[1]

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--snapshot',type=Path,required=True)
    ap.add_argument('--backend-image',required=True)
    ap.add_argument('--acquisition-image',required=True)
    ap.add_argument('--scraper-source',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--sdk-data',type=Path,help='Respuestas ya descargadas (round_1.json y raw-*.jsonl): la adquisición '
                    'sale por el flujo normal del SDK y su navegador contra un sitio de 127.0.0.1, con sandbox y perfil gestionado')
    ap.add_argument('--seccomp',type=Path,default=ROOT/'docker/acquisition/seccomp-linux-amd64.json')
    a=ap.parse_args()
    if a.snapshot.name=='real-jaen.sqlite':raise ValueError('Use an identified inactive snapshot, never the active base')
    if a.output.exists():raise FileExistsError('Report directory must be new')
    a.output.mkdir(parents=True)
    def docker(*args,check=True):
        p=subprocess.run(['docker',*map(str,args)],capture_output=True,text=True,encoding='utf8',errors='replace',timeout=600)
        if check and p.returncode:raise RuntimeError(p.stdout+'\n'+p.stderr)
        return p
    docker('version')
    backend=json.loads(docker('image','inspect',a.backend_image).stdout)[0]
    acquisition=json.loads(docker('image','inspect',a.acquisition_image).stdout)[0]
    sdk_files=('services/pipeline.py','services/planning.py','services/listing.py','services/export.py','store/export.py','store/backup.py','slices.py','sports.py','status.py')
    rev=subprocess.run(['git','-C',str(a.scraper_source),'rev-parse','HEAD'],check=True,capture_output=True,text=True).stdout.strip()
    assert rev=='611e436a1528b0f487aa56a045f023e8ae8ae098'
    sdk_hashes={n:hashlib.sha256((a.scraper_source/'sofascore_scraper'/n).read_bytes()).hexdigest() for n in sdk_files}
    probe="import pathlib,hashlib,json;print(json.dumps({n:hashlib.sha256((pathlib.Path('/opt/scraper/sofascore_scraper')/n).read_bytes()).hexdigest() for n in "+repr(sdk_files)+"}))"
    bundled=json.loads(docker('run','--rm','--init','--pull','never','--network','none','--cap-drop','ALL','--security-opt','no-new-privileges:true','--entrypoint','python',acquisition['Id'],'-B','-c',probe).stdout)
    assert bundled==sdk_hashes,'SDK sources differ'
    for img in (backend,acquisition):
        assert img['Os']=='linux' and img['Config']['User']=='10001:10001'
    sys.path.insert(0,str(ROOT))
    from persistencia.sqlite import copia_consistente,comprobar_copia
    from acquisition.pilot_bundle import producer_rules
    prefix='rj-pilot-v2-'+uuid.uuid4().hex[:12]
    volumes={k:prefix+'-'+k for k in ('state','exchange','cache')}
    created=[];containers=[]
    common=['--rm','--init','--pull','never','--network','none','--cap-drop','ALL','--security-opt','no-new-privileges:true']
    # Chromium del SDK: sandbox con la política fijada de cinco reglas (no el seccomp por defecto) y memoria compartida.
    browser=['--security-opt','seccomp='+str(a.seccomp.resolve()),'--shm-size','512m']
    def run(image,mounts,env,command,label,extra=(),variables=()):
        name=prefix+'-'+label;containers.append(name)
        cmd=['run',*common,*extra,'--name',name,'--label','rj.pilot.v2='+prefix]
        for source,target,kind,readonly in mounts:
            cmd+=['--mount',f'type={kind},source={source},target={target}'+(',readonly' if readonly else '')]
        for variable in variables:cmd+=['--env',variable]
        cmd+=['--env','PYTHONPATH='+env,'--entrypoint','python',image,'-B',*command]
        p=docker(*cmd,check=False);(a.output/(label+'.log')).write_text(p.stdout+p.stderr,encoding='utf8')
        if p.returncode:raise RuntimeError('Stopped at '+label+'\n'+p.stdout+'\n'+p.stderr)
        print(p.stdout[-1800:]);return p
    try:
        for volume in volumes.values():
            assert docker('volume','inspect',volume,check=False).returncode!=0
            docker('volume','create','--label','rj.pilot.v2='+prefix,volume);created.append(volume)
        with tempfile.TemporaryDirectory(prefix=prefix+'-') as tmp:
            copied=Path(tmp)/'inactive-snapshot.sqlite';copia_consistente(a.snapshot,copied);description=comprobar_copia(copied)
            # Only backend can mount state or snapshot. Acquisition gets exchange and its own cache.
            backend_mounts=[(ROOT,'/app','bind',True),(volumes['state'],'/state','volume',False),(volumes['exchange'],'/imports','volume',False)]
            run(backend['Id'],backend_mounts+[(copied,'/state/input.sqlite','bind',True)],'/app:/app/tests',
                ['/app/docker/acquisition/pilot_backend_check.py','prepare'],'prepare')
            acq_mounts=[(ROOT/'acquisition','/opt/app/acquisition','bind',True),
                        (ROOT/'persistencia/bundle_v2.py','/opt/app/persistencia/bundle_v2.py','bind',True),
                        (ROOT/'api.py','/opt/app/api.py','bind',True),(ROOT/'importar.py','/opt/app/importar.py','bind',True),
                        (ROOT/'docker/acquisition/test_pilot_v2.py','/opt/checks/test_pilot_v2.py','bind',True),
                        (volumes['exchange'],'/exchange','volume',False),(volumes['cache'],'/profiles','volume',False)]
            run(acquisition['Id'],acq_mounts,'/opt/app:/opt/scraper:/opt/checks',['-m','unittest','test_pilot_v2','-v'],'transport-tests')
            if a.sdk_data is None:
                run(acquisition['Id'],acq_mounts,'/opt/app:/opt/scraper:/opt/checks',['/opt/checks/test_pilot_v2.py','collect'],'acquisition')
            else:
                # Certificado efímero no confiable (solo para comprobar que el navegador y curl lo rechazan).
                cert,key=Path(tmp)/'cert.pem',Path(tmp)/'key.pem'
                subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','2','-subj','/CN=127.0.0.1',
                                '-addext','subjectAltName=IP:127.0.0.1','-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
                key.chmod(0o644)
                sdk_mounts=acq_mounts+[(a.sdk_data.resolve(),'/fixtures/downloaded','bind',True),(Path(tmp),'/fixtures/tls','bind',True),
                                       (ROOT/'docker/acquisition/sdk_fixture.py','/opt/checks/sdk_fixture.py','bind',True),
                                       (ROOT/'docker/acquisition/test_sdk_adapter.py','/opt/checks/test_sdk_adapter.py','bind',True)]
                variables=('RJ_REQUIRE_SANDBOX=1','RJ_SDK_DATA=/fixtures/downloaded',
                           'RJ_TEST_CERT=/fixtures/tls/cert.pem','RJ_TEST_KEY=/fixtures/tls/key.pem')
                run(acquisition['Id'],sdk_mounts,'/opt/app:/opt/scraper:/opt/checks',['/opt/checks/test_sdk_adapter.py'],
                    'sdk-adapter-tests',browser,variables)
                run(acquisition['Id'],sdk_mounts,'/opt/app:/opt/scraper:/opt/checks',
                    ['/opt/checks/test_pilot_v2.py','collect-sdk','/fixtures/downloaded'],'acquisition',browser)
            run(backend['Id'],backend_mounts,'/app:/app/tests',
                ['/app/docker/acquisition/pilot_backend_check.py','receive',producer_rules()],'receive')
            # Export only metadata reports; never snapshots, journals, credentials or bundles.
            p=docker('run',*common,'--mount',f'type=volume,source={volumes["exchange"]},target=/imports,readonly',
                     '--entrypoint','python',backend['Id'],'-c',
                     "import pathlib,json;print(json.dumps({n:json.loads((pathlib.Path('/imports')/n).read_bytes()) for n in ('prepare-report.json','receive-report.json','bundle-meta.json','sdk-report.json') if (pathlib.Path('/imports')/n).exists()}))")
            report=dict(project=prefix,backend_image=backend['Id'],acquisition_image=acquisition['Id'],
                        network='none',scraper_revision=rev,sdk_hashes=sdk_hashes,source_revision=description['revision'],external_requests=0,
                        transport='sdk' if a.sdk_data else 'urllib',sdk_data=str(a.sdk_data) if a.sdk_data else None,
                        volumes=volumes,production_provider_enabled=False,results=json.loads(p.stdout))
            (a.output/'results.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    finally:
        # Remove only resources with this unpredictable execution label, including failed runs.
        for name in containers:
            p=docker('inspect',name,check=False)
            if p.returncode==0 and json.loads(p.stdout)[0]['Config']['Labels'].get('rj.pilot.v2')==prefix:docker('rm','-f',name)
        for volume in created:
            value=json.loads(docker('volume','inspect',volume).stdout)[0]
            if value['Labels'].get('rj.pilot.v2')!=prefix:raise RuntimeError('Volume ownership changed')
            docker('volume','rm',volume)

if __name__=='__main__':main()
