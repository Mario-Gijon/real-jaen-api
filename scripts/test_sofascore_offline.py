"""Run only recipe acceptance checks: existing image, no pulls/network/host data mounts."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
SDK_FILES=('services/pipeline.py','services/planning.py','services/listing.py','slices.py','sports.py','status.py','services/query.py')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--image',required=True)
    ap.add_argument('--scraper-source',type=Path,required=True,help='Read-only checkout at the pinned revision')
    ap.add_argument('--result',type=Path,required=True,help='New metadata-only report, no traces/profiles')
    args=ap.parse_args()
    def call(command,**kwargs):
        return subprocess.run(command,text=True,encoding='utf8',errors='replace',capture_output=True,**kwargs)
    engine=call(['docker','version','--format','{{.Server.Version}}'],check=True)
    image=json.loads(call(['docker','image','inspect',args.image],check=True).stdout)[0]
    if (image['Os'],image['Architecture'],image['Config']['User']) != ('linux','amd64','10001:10001'):
        raise RuntimeError('The reviewed sandbox requires Linux amd64 and UID 10001')
    revision=call(['git','-C',str(args.scraper_source),'rev-parse','HEAD'],check=True).stdout.strip()
    if revision!='611e436a1528b0f487aa56a045f023e8ae8ae098':
        raise RuntimeError('Scraper revision mismatch')
    expected={name:hashlib.sha256((args.scraper_source/'sofascore_scraper'/name).read_bytes()).hexdigest() for name in SDK_FILES}
    policy=ROOT/'docker/acquisition/seccomp-linux-amd64.json'
    policy_sha=hashlib.sha256(policy.read_bytes()).hexdigest()
    if policy_sha!='abd5cc8ed37eb8a76b57b6aa9e8042e79ac7f04cb189782e2c8565c0765c5cb7':
        raise RuntimeError('Reviewed sandbox policy mismatch')
    # Anonymous disposable container filesystem, not the usual /profiles volume or any sporting SQLite.
    name='rj-recipe-offline-'+uuid.uuid4().hex[:12]
    cmd=['docker','run','--rm','--init','--name',name,'--pull','never','--network','none','--cap-drop','ALL',
         '--security-opt','no-new-privileges:true','--security-opt','seccomp='+str(policy),'--shm-size','512m',
         '--mount',f'type=bind,source={ROOT / "acquisition"},target=/opt/app/acquisition,readonly',
         '--mount',f'type=bind,source={ROOT / "docker" / "acquisition" / "test_sofascore_offline.py"},target=/opt/checks/test_sofascore_offline.py,readonly',
         '--env','PYTHONPATH=/opt/app:/opt/scraper:/opt/checks','--entrypoint','python',image['Id'],'-B','-c']
    probe="import pathlib,hashlib,json;print(json.dumps({n:hashlib.sha256((pathlib.Path('/opt/scraper/sofascore_scraper')/n).read_bytes()).hexdigest() for n in "+repr(SDK_FILES)+"}))"
    audited=call(cmd+[probe],check=True)
    if json.loads(audited.stdout)!=expected:
        raise RuntimeError('Bundled SDK differs from reviewed source')
    # Reuse the name only after --rm confirmed the prior container has exited.
    tests=call(cmd+["import unittest; suite=unittest.defaultTestLoader.loadTestsFromName('test_sofascore_offline'); result=unittest.TextTestRunner(verbosity=2).run(suite); print('CHECK_COUNT='+str(result.testsRun)); raise SystemExit(not result.wasSuccessful())"])
    sys.stdout.write(tests.stdout);sys.stderr.write(tests.stderr)
    count=next((int(line.split('=')[1]) for line in tests.stdout.splitlines() if line.startswith('CHECK_COUNT=')),None)
    evidence=dict(engine=engine.stdout.strip(),image=image['Id'],scraper_revision=revision,sdk_sha256=expected,
                  platform='Docker Desktop Linux amd64',seccomp_sha256=policy_sha,network='none',uid=10001,
                  capabilities=[],no_new_privileges=True,init=True,tests=count,exit_code=tests.returncode,
                  external_requests=0,host_data_mounts=[],production_provider_enabled=False)
    with args.result.open('x',encoding='utf8',newline='\n') as out:
        json.dump(evidence,out,indent=2);out.write('\n')
    return tests.returncode

if __name__=='__main__':raise SystemExit(main())
