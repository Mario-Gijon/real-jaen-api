"""Offline Linux profile recovery acceptance test. Needs existing local candidate image.
No pulls/network/host data. All names and storage new. Docker CLI host is trusted fencing authority.
python3 docker/scraper/test_profile_recovery.py --image LOCAL_IMAGE --evidence /new/absolute/path
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid
from test_full_sandbox import docker, read_json, HERE

LABEL='rj.profile.recovery'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--evidence',required=True,type=Path)
    args=parser.parse_args()
    out=args.evidence.resolve(); out.mkdir(parents=True,exist_ok=False)
    policy=HERE/'policies/docker29.5.2-cap0-amd64.full-candidate.approved-probe.json'
    assert hashlib.sha256(policy.read_bytes()).hexdigest()=='e340dc1b775e61332d5f9dcf63c6bb2a2f614a02fda2555fad363b2bd4d6fe1b'
    image=json.loads(docker('image','inspect',args.image).stdout)[0]
    assert image['Os']=='linux' and image['Architecture']=='amd64'
    job='rj-profile-recover-'+uuid.uuid4().hex[:10]; volume=job+'-only'
    assert docker('volume','inspect',volume,check=False).returncode!=0
    docker('volume','create','--label',f'{LABEL}={job}',volume)
    result={'job':job,'volume':volume,'image':image['Id'],'profile_sha256':hashlib.sha256(policy.read_bytes()).hexdigest(),'phases':[]}
    def save(): (out/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    save()
    (out/'docker-version.txt').write_text(docker('version').stdout)
    def phase(key,hold=False,kill=False,proof=None,inject=False,expect=0):
        name=job+'-'+key
        cmd=['create','--pull','never','--name',name,'--hostname',key+'-'+uuid.uuid4().hex[:8],
             '--label',f'{LABEL}={job}','--init','--network','none','--user','10001:10001',
             '--cap-drop','ALL','--security-opt','no-new-privileges:true','--security-opt',f'seccomp={policy}',
             '--shm-size','512m','--mount',f'type=volume,source={volume},target=/acquisition',
             '--entrypoint','python',args.image,'/opt/probe/profile_owner_probe.py','--container-id',name]
        if proof: cmd+=['--fence','/tmp/fence.json']
        if inject: cmd+=['--inject-failure','after-cleanup']
        cmd+=['--','--phase','browser','--report',key+'.json']
        if key!='seed': cmd+=['--expect-cookie']
        if hold: cmd+=['--hold']
        docker(*cmd)
        for file in ('offline_probe.py','profile_owner_probe.py'):
            docker('cp',HERE/file,f'{name}:/opt/probe/{file}')
        if proof:
            fencefile=out/(key+'.fence.json'); fencefile.write_text(json.dumps(proof,indent=2))
            docker('cp',fencefile,f'{name}:/tmp/fence.json')
        inspect=json.loads(docker('inspect',name).stdout)[0]; host=inspect['HostConfig']
        assert host['NetworkMode']=='none' and not host['Privileged'] and host['CapDrop']==['ALL'] and not host['CapAdd']
        assert not host['PortBindings'] and host['Init'] and inspect['Config']['User']=='10001:10001'
        assert len(inspect['Mounts'])==1 and inspect['Mounts'][0]['Name']==volume
        assert json.loads(next(x[8:] for x in host['SecurityOpt'] if x.startswith('seccomp=')))==read_json(policy)
        assert any('no-new-privileges' in x for x in host['SecurityOpt'])
        (out/(key+'.inspect.json')).write_text(json.dumps(inspect,indent=2))
        docker('start',name)
        if hold:
            deadline=time.monotonic()+45
            # A phase-specific report marker avoids stale ready.json from the previous process.
            while time.monotonic()<deadline:
                check=docker('exec',name,'python','-c',"import json,pathlib; p=pathlib.Path('/acquisition/ready.json'); s=pathlib.Path('/acquisition/profile-control/session.json'); assert p.exists(); r=json.loads(p.read_text()); t=json.loads(s.read_text()); assert t['dirty']['container_id']=="+repr(name)+"; assert r['profile_session_nonce']==t['dirty']['nonce']",check=False)
                if check.returncode==0: break
                assert json.loads(docker('inspect',name).stdout)[0]['State']['Running'], 'Session failed before ready'
                time.sleep(.25)
            else: raise RuntimeError('Readiness timeout')
            docker('cp',f'{name}:/acquisition/ready.json',out/(key+'.ready.json'))
            ready=read_json(out/(key+'.ready.json'))
            assert ready['sandbox_proc_verified'] and ready['browser_cookie_previous']
            if key=='crash':
                # Two contenders against the same mounted inode while the first owner/browser is live.
                command=['docker','exec',name,'python','/opt/probe/profile_owner_probe.py','--container-id','contender','--','--phase','browser']
                contenders=[subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
                for i,p in enumerate(contenders):
                    stdout,stderr=p.communicate(timeout=15)
                    (out/f'contender-{i}.log').write_text(stdout+stderr)
                    assert p.returncode==73 and json.loads(stdout)['status']=='profile_busy'
                result['concurrent_contenders_rejected']=2
            (out/(key+'.top-before.txt')).write_text(docker('top',name,'-eo','pid,ppid,args').stdout)
            if kill: docker('kill','--signal','KILL',name)
            else: docker('stop','--time','30',name)
        docker('wait',name,timeout=45)
        final=json.loads(docker('inspect',name).stdout)[0]
        logs=docker('logs',name); (out/(key+'.log')).write_text(logs.stdout+logs.stderr)
        assert not final['State']['Running'] and final['State']['Pid']==0
        expected=137 if kill else expect
        assert final['State']['ExitCode']==expected,(key,final['State'],logs.stdout[-2000:])
        docker('cp',f'{name}:/acquisition/profile-control/session.json',out/(key+'.state.json'))
        state=read_json(out/(key+'.state.json'))
        if not kill and expect==0:
            docker('cp',f'{name}:/acquisition/{key}.json',out/(key+'.json'))
            report=read_json(out/(key+'.json')); assert report['ok'] and report['children_closed'] and report['sandbox_proc_verified']
            if key!='seed': assert report['browser_cookie_previous']
            docker('cp',f'{name}:/acquisition/profile-control/last-success.json',out/(key+'.preserved.json'))
            preserved=read_json(out/(key+'.preserved.json')); assert preserved['backup_unchanged'] and preserved['source_unchanged']
            assert state['dirty'] is None
        if expect==74:
            docker('cp',f'{name}:/acquisition/profile-control/last-failure.json',out/(key+'.failure.json'))
            failed=read_json(out/(key+'.failure.json')); assert failed['status']=='recovery_required'
            if inject: assert failed['backup_unchanged'] and failed['source_unchanged']
        result['phases'].append({'phase':key,'exit_code':expected,'container':name,'hostname':inspect['Config']['Hostname'],'runtime_id':inspect['Id'],'runtime_pid_after':0,'ok':True})
        save(); print(key+': PASS',flush=True)
        return name,state
    phase('seed')
    old,state=phase('crash',hold=True,kill=True)
    # A free flock after SIGKILL is deliberately insufficient without host fencing.
    _,blocked=phase('unfenced',expect=74)
    assert blocked==state
    def fence(name,state):
        old=json.loads(docker('inspect',name).stdout)[0]
        assert old['Config']['Labels'][LABEL]==job and old['State']['Pid']==0 and not old['State']['Running']
        assert old['State']['Status']=='exited' and old['State']['ExitCode']==137
        assert len(old['Mounts'])==1 and old['Mounts'][0]['Name']==volume
        assert state['dirty']['container_id']==name
        return {'fenced':True,'container_id':name,'nonce':state['dirty']['nonce'],'runtime_id':old['Id'],
                'reason':'Trusted Docker host inspected this uniquely labelled former owner: exited, PID 0, same exclusive volume'}
    _,wrong = phase('wrong-fence',proof={'fenced': True, 'container_id': old, 'nonce': 'not-the-recorded-session'},expect=74)
    assert wrong == state
    phase('recovered-other-host',proof=fence(old,state))
    phase('sigterm',hold=True)
    old,state=phase('crash-again',hold=True,kill=True)
    phase('recovery-failure',proof=fence(old,state),inject=True,expect=74)
    result['ok']=True; result['scope']='Diagnostic adapter only; scraper checkout unchanged; no integration'
    save()
    for entry in result['phases']:
        inspect=json.loads(docker('inspect',entry['container']).stdout)[0]
        assert not inspect['State']['Running'] and inspect['Config']['Labels'][LABEL]==job
        docker('rm',entry['container'])
    print(f'PASS. Evidence {out}; only test volume retained: {volume}',flush=True)

if __name__=='__main__': main()
