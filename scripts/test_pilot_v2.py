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
    a=ap.parse_args()
    if a.snapshot.name=='real-jaen.sqlite':raise ValueError('Use an identified inactive snapshot, never the active base')
    if a.output.exists():raise FileExistsError('Report directory must be new')
    a.output.mkdir(parents=True)
    def docker(*args,check=True):
        p=subprocess.run(['docker',*map(str,args)],capture_output=True,text=True,encoding='utf8',errors='replace',timeout=240)
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
    def run(image,mounts,env,command,label):
        name=prefix+'-'+label;containers.append(name)
        cmd=['run',*common,'--name',name,'--label','rj.pilot.v2='+prefix]
        for source,target,kind,readonly in mounts:
            cmd+=['--mount',f'type={kind},source={source},target={target}'+(',readonly' if readonly else '')]
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
            run(acquisition['Id'],acq_mounts,'/opt/app:/opt/scraper:/opt/checks',['/opt/checks/test_pilot_v2.py','collect'],'acquisition')
            run(backend['Id'],backend_mounts,'/app:/app/tests',
                ['/app/docker/acquisition/pilot_backend_check.py','receive',producer_rules()],'receive')
            # Export only metadata reports; never snapshots, journals, credentials or bundles.
            p=docker('run',*common,'--mount',f'type=volume,source={volumes["exchange"]},target=/imports,readonly',
                     '--entrypoint','python',backend['Id'],'-c',
                     "import pathlib,json;print(json.dumps({n:json.loads((pathlib.Path('/imports')/n).read_bytes()) for n in ('prepare-report.json','receive-report.json','bundle-meta.json')}))")
            report=dict(project=prefix,backend_image=backend['Id'],acquisition_image=acquisition['Id'],
                        network='none',scraper_revision=rev,sdk_hashes=sdk_hashes,source_revision=description['revision'],external_requests=0,
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
