"""Portable offline acceptance: existing image, exclusive resources, no pulls or external network."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import uuid
import time
HERE = Path(__file__).resolve().parent

def docker(*args, check=True, timeout=60):
    return subprocess.run(['docker', *map(str, args)], check=check, timeout=timeout, text=True, encoding='utf-8', errors='replace', stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--image', required=True)
    ap.add_argument('--evidence', type=Path, required=True)
    args = ap.parse_args()
    out = args.evidence.resolve()
    out.mkdir(parents=True, exist_ok=False)
    image = json.loads(docker('image', 'inspect', args.image).stdout)[0]
    assert image['Os'] == 'linux' and image['Architecture'] == 'amd64'
    policy = HERE / 'seccomp-linux-amd64.json'
    assert hashlib.sha256(policy.read_bytes()).hexdigest() == 'abd5cc8ed37eb8a76b57b6aa9e8042e79ac7f04cb189782e2c8565c0765c5cb7'
    job = 'rj-profile-impl-' + uuid.uuid4().hex[:10]
    volume = job + '-profiles'
    label = 'rj.profile.implementation'
    assert docker('volume', 'inspect', volume, check=False).returncode != 0
    docker('volume', 'create', '--label', f'{label}={job}', volume)
    results = {'job': job, 'volume': volume, 'image': image['Id'], 'seccomp_sha256': hashlib.sha256(policy.read_bytes()).hexdigest(), 'cases': []}
    (out / 'docker-version.txt').write_text(docker('version').stdout, encoding='utf-8')
    names = []

    def save():
        (out / 'results.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    save()

    def run(key, arguments, *, fixture=False, expected=0, hold=None, tmpfs=False):
        name = job + '-' + key
        names.append(name)
        cmd = ['create', '--pull', 'never', '--name', name, '--hostname', key + '-' + uuid.uuid4().hex[:8], '--label', f'{label}={job}', '--init', '--network', 'none', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--security-opt', f'seccomp={policy}', '--shm-size', '512m']
        if tmpfs:
            cmd += ['--tmpfs', '/profiles:rw,size=1m,uid=10001,gid=10001,mode=0700']
        else:
            cmd += ['--mount', f'type=volume,source={volume},target=/profiles']
        cmd += ['--entrypoint', 'python', args.image, '-B', '-m', 'offline_fixture' if fixture else 'acquisition.admin', *arguments]
        docker(*cmd)
        inspect = json.loads(docker('inspect', name).stdout)[0]
        host = inspect['HostConfig']
        assert host['NetworkMode'] == 'none' and host['CapDrop'] == ['ALL'] and (not host['CapAdd']) and (not host['Privileged']) and (not host['PortBindings'])
        assert inspect['Config']['User'] == '10001:10001' and host['Init']
        assert json.loads(next((x[8:] for x in host['SecurityOpt'] if x.startswith('seccomp=')))) == json.loads(policy.read_text(encoding='utf-8'))
        assert any(('no-new-privileges' in x for x in host['SecurityOpt']))
        if not tmpfs:
            assert len(inspect['Mounts']) == 1 and inspect['Mounts'][0]['Name'] == volume and (inspect['Mounts'][0]['Destination'] == '/profiles')
        (out / (key + '.inspect.json')).write_text(json.dumps(inspect, indent=2), encoding='utf-8')
        docker('start', name)
        if hold:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                ready = docker('exec', name, 'test', '-f', '/profiles/ready-' + inspect['Config']['Hostname'] + '.json', check=False)
                if ready.returncode == 0:
                    break
                assert json.loads(docker('inspect', name).stdout)[0]['State']['Running'], 'Failed before readiness'
                time.sleep(0.25)
            else:
                raise RuntimeError('Readiness timeout')
            if hold == 'concurrent':
                commands = [['verify-profile'], ['recover-profile', '--confirm-service-stopped', '--confirm-no-external-users']]
                procs = [subprocess.Popen(['docker', 'exec', name, 'python', '-B', '-m', 'acquisition.admin', *cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for cmd in commands]
                for i, proc in enumerate(procs):
                    stdout, stderr = proc.communicate(timeout=15)
                    (out / f'concurrent-{i}.log').write_text(stdout + stderr, encoding='utf-8')
                    assert proc.returncode == 73, (stdout, stderr)
            (out / (key + '.top.txt')).write_text(docker('top', name, '-eo', 'pid,ppid,args').stdout, encoding='utf-8')
            docker('kill', '--signal', 'KILL', name) if hold in ('concurrent', 'kill') else docker('stop', '--time', '30', name)
        docker('wait', name, timeout=45)
        final = json.loads(docker('inspect', name).stdout)[0]
        log = docker('logs', name)
        (out / (key + '.log')).write_text(log.stdout + log.stderr, encoding='utf-8')
        if not tmpfs and key not in ('help', 'init-no-confirm'):
            docker('cp', f'{name}:/profiles/control/.', out / (key + '.control'))
        case = {'case': key, 'expected': expected, 'actual': final['State']['ExitCode'], 'hostname': inspect['Config']['Hostname'], 'ok': final['State']['ExitCode'] == expected, 'runtime_pid': final['State']['Pid']}
        results['cases'].append(case)
        save()
        assert case['ok'] and (not final['State']['Running']) and (case['runtime_pid'] == 0), (key, log.stdout, log.stderr)
        print(key + ': PASS', flush=True)
        return final
    run('help', ['--help'])
    run('init-no-confirm', ['initialize-profile'], expected=74)
    run('init', ['initialize-profile', '--confirm-new-profile'])
    run('init-again', ['initialize-profile', '--confirm-new-profile'], expected=74)
    run('clean', ['verify-profile'])
    run('seed', ['seed'], fixture=True)
    run('crash', ['hold'], fixture=True, hold='concurrent', expected=137)
    run('dirty-no-auto', ['verify-profile'], expected=74)
    run('recover-no-confirm', ['recover-profile'], expected=74)
    run('recover-one-confirm', ['recover-profile', '--confirm-service-stopped'], expected=74)
    run('recover', ['recover-profile', '--confirm-service-stopped', '--confirm-no-external-users'])
    run('cookie-other-host', ['probe'], fixture=True)
    run('sigterm', ['hold'], fixture=True, hold='term')
    run('after-sigterm', ['verify-profile'])
    for boundary in ('before', 'after'):
        run('boundary-' + boundary, ['crash-' + boundary], fixture=True, expected=137)
        run('boundary-' + boundary + '-blocked', ['verify-profile'], expected=74)
        run('boundary-' + boundary + '-recover', ['recover-profile', '--confirm-service-stopped', '--confirm-no-external-users'])
        run('boundary-' + boundary + '-cookie', ['probe'], fixture=True)
    run('enospc', ['real-enospc'], fixture=True, tmpfs=True)
    results['ok'] = True
    save()
    for name in names:
        info = json.loads(docker('inspect', name).stdout)[0]
        assert not info['State']['Running'] and info['Config']['Labels'][label] == job
        docker('rm', name)
    print(f'PASS; evidence {out}; exclusive volume retained {volume}', flush=True)
if __name__ == '__main__':
    main()
