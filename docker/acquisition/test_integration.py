"""Drive only the explicitly simulated Compose in new exclusive resources."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acquisition-image',required=True);parser.add_argument('--backend-image',required=True)
    parser.add_argument('--evidence',required=True,type=Path);args=parser.parse_args()
    out=args.evidence.resolve();out.mkdir(parents=True,exist_ok=False)
    job='rj-acq-sim-'+uuid.uuid4().hex[:10]
    env=os.environ.copy();token=secrets.token_hex(32)
    secret=out/'internal-secret';secret.write_text(token,encoding='ascii');secret.chmod(0o600)
    env.update(RJ_SIM_ACQUISITION_IMAGE=args.acquisition_image,RJ_SIM_BACKEND_IMAGE=args.backend_image,
        RJ_ENABLE_SIMULATED='1',RJ_SIM_SECRET_FILE=str(secret),RJ_SIM_PROFILE_VOLUME=job+'-profiles',
        RJ_SIM_JOBS_VOLUME=job+'-jobs',RJ_SIM_STATE_VOLUME=job+'-state')
    base=['docker','compose','-p',job,'-f',str(ROOT/'docker/acquisition/compose.simulated.yaml'),'--profile','simulated']
    results={'project':job,'cases':[],'volumes':[env[k] for k in ['RJ_SIM_PROFILE_VOLUME','RJ_SIM_JOBS_VOLUME','RJ_SIM_STATE_VOLUME']]}
    def save(): (out/'results.json').write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8')
    def command(args,expect=0,name=None,timeout=120):
        result=subprocess.run(args,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
        combined=result.stdout+result.stderr
        assert token.encode() not in combined,'Secret leaked in command output'
        if name:
            (out/(name+'.log')).write_bytes(combined)
            results['cases'].append({'case':name,'exit':result.returncode,'ok':result.returncode==expect});save()
        assert result.returncode==expect,(name,combined.decode(errors='replace')[-2500:])
        return result.stdout
    for image in (args.acquisition_image,args.backend_image):command(['docker','image','inspect',image])
    command(['docker','run','--rm','--pull','never','--network','none','--cap-drop','ALL','--security-opt','no-new-privileges:true',args.acquisition_image],expect=1,name='simulation-disabled-by-default')
    for volume in results['volumes']:
        missing=subprocess.run(['docker','volume','inspect',volume],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        assert missing.returncode!=0
        command(['docker','volume','create','--label','rj.acquisition.simulated='+job,volume])
    save()
    config=command(base+['config','--format','json'])
    parsed=json.loads(config)
    assert parsed['networks']['isolated']['internal'] is True
    assert len(parsed['services']['backend-client']['security_opt'])==1
    assert parsed['services']['backend-client']['security_opt'][0]=='no-new-privileges:true'
    (out/'compose-config.json').write_bytes(config)
    try:
        command(base+['run','--rm','--no-deps','profile-admin','initialize-profile','--confirm-new-profile'],name='initialize-profile')
        command(base+['run','--rm','--no-deps','backend-client','init'],name='initialize-synthetic-database')
        command(base+['up','-d','--no-deps','acquisition'],name='start-acquisition')
        def ready():
            for _ in range(60):
                r=subprocess.run(base+['exec','-T','acquisition','python','-c',"import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8002/health',timeout=1).status==200"],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                if r.returncode==0:return
                time.sleep(.1)
            raise RuntimeError('Acquisition not ready')
        ready()
        raw=command(base+['ps','-q','acquisition']).decode().strip()
        inspect=json.loads(command(['docker','inspect',raw]))[0]
        assert len(inspect['Mounts'])==3 and all(v['Destination'] not in ('/state','/imports') for v in inspect['Mounts'])
        assert not inspect['HostConfig']['Privileged'] and inspect['HostConfig']['CapDrop']==['ALL']
        (out/'acquisition-inspect.json').write_text(json.dumps(inspect,indent=2),encoding='utf-8')
        # Two one-off backend images run the real coordinator and CLI with default seccomp.
        command(base+['run','--rm','--no-deps','backend-client','units'],name='protocol-unit-tests')
        command(base+['run','--rm','--no-deps','backend-client','auth-probe'],name='live-authentication')
        command(base+['run','--rm','--no-deps','backend-client','flow'],name='complete-flow')
        command(base+['run','--rm','--no-deps','backend-client','restart-start'],name='queue-before-crash')
        command(base+['kill','-s','SIGKILL','acquisition'],name='kill-only-test-acquisition')
        command(base+['up','-d','--force-recreate','--no-deps','acquisition'],name='restart-only-test-acquisition')
        ready()
        command(base+['run','--rm','--no-deps','backend-client','restart-check'],name='no-automatic-resume')
        rawjob=command(base+['run','--rm','--no-deps','backend-client','tamper-start'],name='prepare-sealed-bundle').decode().strip()
        assert str(uuid.UUID(rawjob))==rawjob
        code="from pathlib import Path;p=Path('/acquisition-jobs/bundles')/"+repr(rawjob+'.json')+";p.chmod(0o600);p.write_bytes(p.read_bytes()+b'changed')"
        command(base+['exec','-T','acquisition','python','-c',code],name='tamper-only-test-bundle')
        command(base+['run','--rm','--no-deps','backend-client','tamper-check'],name='reject-tamper-before-publication')
        logs=command(base+['logs','--no-color'],name='service-logs-secret-audit')
        assert token.encode() not in logs
        command(base+['down','--remove-orphans'],name='stop-only-test-stack')
        secret.unlink()  # No retained secret, never copied to repository evidence.
        results['ok']=True;results['secret_removed']=True;save()
        print('PASS '+str(out),flush=True)
    finally:
        cleanup=subprocess.run(base+['down','--remove-orphans'],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        assert token.encode() not in cleanup.stdout+cleanup.stderr
        (out/'cleanup.log').write_bytes(cleanup.stdout+cleanup.stderr)
        secret.unlink(missing_ok=True)

if __name__=='__main__':main()
