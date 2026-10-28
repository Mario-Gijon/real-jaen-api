"""Build only from a locally present image ID and an explicit source whitelist; no pulls/install."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime-image',required=True);p.add_argument('--tag',required=True)
    a=p.parse_args()
    source=json.loads(subprocess.run(['docker','image','inspect',a.runtime_image],check=True,capture_output=True,text=True).stdout)[0]
    if (source['Os'],source['Config']['User'])!=('linux','10001:10001'):raise ValueError('Reviewed unprivileged Linux runtime required')
    if subprocess.run(['docker','image','inspect',a.tag],capture_output=True).returncode==0:raise FileExistsError('Do not overwrite an existing tag')
    with tempfile.TemporaryDirectory(prefix='rj-pilot-build-') as temporary:
        stage=Path(temporary);(stage/'acquisition').mkdir();(stage/'persistencia').mkdir()
        for f in (ROOT/'acquisition').glob('*.py'):shutil.copyfile(f,stage/'acquisition'/f.name)
        shutil.copyfile(ROOT/'acquisition/sofascore-scope.json',stage/'acquisition/sofascore-scope.json')
        for f in ('api.py','importar.py'):shutil.copyfile(ROOT/f,stage/f)
        shutil.copyfile(ROOT/'persistencia/bundle_v2.py',stage/'persistencia/bundle_v2.py')
        shutil.copyfile(ROOT/'docker/acquisition/Dockerfile.pilot',stage/'Dockerfile')
        # Classic builder uses the local immutable image ID directly. No registry resolution,
        # networked RUN, package installation or daemon/global configuration change.
        environment=dict(os.environ,DOCKER_BUILDKIT='0')
        subprocess.run(['docker','build','--pull=false','--network=none','--build-arg','PROFILE_IMAGE='+source['Id'],
                        '-t',a.tag,str(stage)],check=True,env=environment)
    subprocess.run(['docker','run','--rm','--init','--pull','never','--network','none','--cap-drop','ALL',
                    '--security-opt','no-new-privileges:true','--entrypoint','python',a.tag,'-B','-c',
                    "from acquisition.pilot_bundle import producer_rules; from acquisition.pilot_transport import PilotTransport; import persistencia.bundle_v2; print('pilot imports OK; provider default disabled; rules='+producer_rules())"],check=True)

if __name__=='__main__':main()
