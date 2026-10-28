"""Portable offline sandbox acceptance probe. No pulls, network, host data or policy changes.

Run from the backend checkout with Python 3.10+ and Docker Linux available:
python3 docker/scraper/test_full_sandbox.py --image LOCAL_IMAGE --evidence /absolute/new/path
Only new uniquely labelled volumes/containers are created. On failure stop and preserve evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

HERE = Path(__file__).resolve().parent
LABEL = 'rj.sandbox.full'

def docker(*args, timeout=60, check=True):
    return subprocess.run(['docker', *map(str, args)], text=True, encoding='utf-8',
                          errors='replace', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout, check=check)

def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='Existing local image; never pulled')
    parser.add_argument('--evidence', type=Path, required=True, help='New evidence directory, outside Git')
    args = parser.parse_args()
    out = args.evidence.resolve()
    out.mkdir(parents=True, exist_ok=False)
    profile = HERE / 'policies/docker29.5.2-cap0-amd64.full-candidate.approved-probe.json'
    baseline = read_json(HERE / 'policies/docker29.5.2-cap0-amd64.baseline.json')
    policy = read_json(profile)
    rules = [{'names': [name], 'action': 'SCMP_ACT_ALLOW', 'args':
             ([] if value is None else [{'index': 0, 'value': value, 'op': 'SCMP_CMP_EQ'}])}
             for name, value in [('unshare', 0x10000000), ('clone', 0x10000011),
                                ('clone', 0x70000011), ('clone', 0x20000011), ('chroot', None)]]
    assert policy == baseline | {'syscalls': baseline['syscalls'] + rules}
    image = json.loads(docker('image', 'inspect', args.image).stdout)[0]
    assert image['Os'] == 'linux' and image['Architecture'] == 'amd64'
    job = 'rj-full-accept-' + uuid.uuid4().hex[:10]
    volume = job + '-profile'
    assert docker('volume', 'inspect', volume, check=False).returncode != 0
    docker('volume', 'create', '--label', f'{LABEL}={job}', volume)
    result = {'job': job, 'volume': volume, 'image': image['Id'],
              'profile_sha256': hashlib.sha256(profile.read_bytes()).hexdigest(), 'phases': []}
    (out / 'docker-version.txt').write_text(docker('version').stdout)
    (out / 'docker-info.txt').write_text(docker('info').stdout)
    def save():
        (out / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    save()
    for phase, held, crash in [('first', False, False), ('cookie-recreated', False, False),
                               ('sigterm', True, False), ('sigkill', True, True),
                               ('recovered', False, False)]:
        name = job + '-' + phase
        command = ['create', '--pull', 'never', '--name', name, '--label', f'{LABEL}={job}',
                   '--init', '--network', 'none', '--user', '10001:10001', '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges:true', '--security-opt', f'seccomp={profile}',
                   '--shm-size', '512m', '--mount', f'type=volume,source={volume},target=/acquisition',
                   '--entrypoint', 'python', args.image, '/opt/probe/offline_probe.py',
                   '--phase', 'browser', '--report', phase + '.json']
        if phase != 'first':
            command += ['--expect-storage', '--expect-cookie']
        if held:
            command += ['--hold']
        docker(*command)
        docker('cp', HERE / 'offline_probe.py', f'{name}:/opt/probe/offline_probe.py')
        inspect = json.loads(docker('inspect', name).stdout)[0]
        host = inspect['HostConfig']
        assert host['NetworkMode'] == 'none' and not host['Privileged']
        assert host['CapDrop'] == ['ALL'] and not host['CapAdd'] and not host['PortBindings']
        assert inspect['Config']['User'] == '10001:10001' and host['Init']
        assert len(inspect['Mounts']) == 1 and inspect['Mounts'][0]['Name'] == volume
        assert inspect['Mounts'][0]['Destination'] == '/acquisition'
        assert any('no-new-privileges' in v for v in host['SecurityOpt'])
        assert json.loads(next(v[8:] for v in host['SecurityOpt'] if v.startswith('seccomp='))) == policy
        (out / (phase + '.inspect.json')).write_text(json.dumps(inspect, indent=2))
        # Prevent a stale readiness marker from making a subsequent hold test pass.
        if held:
            # Replace startup with a tiny wrapper; never run two browser sessions together.
            wrapper = out / (phase + '-entry.py')
            wrapper.write_text("from pathlib import Path\nimport runpy,sys\nPath('/acquisition/ready.json').unlink(missing_ok=True)\nsys.argv=['/opt/probe/offline_probe.py','--phase','browser','--expect-cookie','--expect-storage','--hold','--report'," + repr(phase+'.json') + "]\nrunpy.run_path('/opt/probe/real_probe.py',run_name='__main__')\n")
            docker('cp', HERE / 'offline_probe.py', f'{name}:/opt/probe/real_probe.py')
            docker('cp', wrapper, f'{name}:/opt/probe/offline_probe.py')
        docker('start', name)
        if held:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                check = docker('exec', name, 'test', '-f', '/acquisition/ready.json', check=False)
                if check.returncode == 0:
                    break
                assert json.loads(docker('inspect', name).stdout)[0]['State']['Running'], 'Browser failed before readiness'
                time.sleep(.25)
            else:
                raise RuntimeError('Readiness timeout')
            (out / (phase + '.top-before.txt')).write_text(docker('top', name, '-eo', 'pid,ppid,args').stdout)
            docker('cp', f'{name}:/acquisition/ready.json', out / (phase + '.ready.json'))
            ready = read_json(out / (phase + '.ready.json'))
            assert ready['browser_ok'] and ready['sandbox_proc_verified'] and ready['browser_cookie_previous']
            if crash:
                docker('kill', '--signal', 'KILL', name)
            else:
                docker('stop', '--time', '30', name)
        docker('wait', name, timeout=45)
        final = json.loads(docker('inspect', name).stdout)[0]
        assert not final['State']['Running'] and final['State']['Pid'] == 0
        logs = docker('logs', name)
        (out / (phase + '.log')).write_text(logs.stdout + logs.stderr)
        expected_code = 137 if crash else 0
        if final['State']['ExitCode'] != expected_code:
            docker('cp', f'{name}:/acquisition/{phase}.json', out / (phase + '.json'), check=False)
            result['ok'] = False
            result['stopped_at'] = phase
            result['phases'].append({'phase': phase, 'ok': False, 'exit_code': final['State']['ExitCode'],
                                     'container': name, 'runtime_pid_after': final['State']['Pid']})
            save()
            raise RuntimeError(f'{phase} failed; stopped without further operations. Inspect {out} and {name}')
        top = docker('top', name, check=False)
        assert top.returncode != 0  # Runtime no longer has a running process group.
        if not crash:
            docker('cp', f'{name}:/acquisition/{phase}.json', out / (phase + '.json'))
            report = read_json(out / (phase + '.json'))
            assert report['ok'] and report['sandbox_proc_verified'] and report['children_closed']
            assert report['local_requests'] and not report['page_requests_denied']
            if phase != 'first':
                assert report['browser_cookie_previous']
        result['phases'].append({'phase': phase, 'exit_code': expected_code,
                                 'container': name, 'runtime_pid_after': 0, 'ok': True})
        save()
        print(phase + ': PASS', flush=True)
        assert final['Config']['Labels'][LABEL] == job
        docker('rm', name)
    result['ok'] = True
    save()
    # Keep only this test volume for review. No automatic deletion or reuse on rerun.
    print(f'PASS. Evidence: {out}; exclusive volume retained: {volume}', flush=True)

if __name__ == '__main__':
    main()
