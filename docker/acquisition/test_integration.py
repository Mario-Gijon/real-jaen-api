"""Drive the isolated acquisition test stack (compose.test.yaml) in new exclusive resources. No network egress.

python -X utf8 docker/acquisition/test_integration.py --acquisition-image IMG --backend-image IMG \
    --snapshot <copias/...sqlite> --evidence <new folder>

The backend client works on a copy of the identified inactive snapshot; the acquisition service replays the original
responses of the sealed real bundle of 2026-10-28 (fixtures/real-bundle-20261028.tar.xz: the bundle, its descriptor and
its inventory, without cookies or secrets), extracted and checked against its sums, then mounted read-only.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
REAL_BUNDLE = Path(__file__).resolve().parent / 'fixtures' / 'real-bundle-20261028.tar.xz'


def extract_real_bundle(target):
    """The recorded real bundle in a new folder readable by the containers' users, each file checked against its sum."""
    target.mkdir()
    with tarfile.open(REAL_BUNDLE) as tar:
        tar.extractall(target, filter='data')
    for line in REAL_BUNDLE.with_suffix('').with_suffix('.sha256').read_text(encoding='ascii').splitlines():
        digest, name = line.split()
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == digest, name
        (target / name).chmod(0o644)
    target.chmod(0o755)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--acquisition-image', required=True)
    parser.add_argument('--backend-image', required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--evidence', required=True, type=Path)
    args = parser.parse_args()
    if args.snapshot.name == 'real-jaen.sqlite' or args.snapshot.parent.name != 'copias':
        raise SystemExit('Only an identified inactive copy from copias/, never the active base')
    out = args.evidence.resolve()
    out.mkdir(parents=True, exist_ok=False)
    project = 'rj-acq-test-' + uuid.uuid4().hex[:10]
    env = os.environ.copy()
    token = secrets.token_hex(32)
    secret_dir = out / '.secrets'
    secret_dir.mkdir(mode=0o700)
    secret = secret_dir / 'internal-secret'
    secret.write_text(token, encoding='ascii')
    # The container runs as UID 10001, so the random token file must be readable through its read-only bind mount.
    # A private parent directory keeps it hidden from other host users until the runner removes it.
    secret.chmod(0o644)
    tmp = tempfile.TemporaryDirectory(prefix=project + '-')
    sys.path.insert(0, str(ROOT))
    from persistencia.sqlite import comprobar_copia, copia_consistente
    snapshot = Path(tmp.name) / 'inactive-snapshot.sqlite'
    copia_consistente(args.snapshot, snapshot)
    routes = Path(tmp.name) / 'routes.json'
    evidence = extract_real_bundle(Path(tmp.name) / 'evidence')
    env.update(RJ_TEST_ACQUISITION_IMAGE=args.acquisition_image, RJ_TEST_BACKEND_IMAGE=args.backend_image, RJ_TEST_MODE='1',
               RJ_TEST_SECRET_FILE=str(secret), RJ_TEST_ROUTES=str(routes), RJ_TEST_SNAPSHOT=str(snapshot),
               RJ_TEST_PROFILE_VOLUME=project + '-profiles', RJ_TEST_JOBS_VOLUME=project + '-jobs',
               RJ_TEST_STATE_VOLUME=project + '-state', RJ_TEST_EVIDENCE_DIR=str(evidence))
    base = ['docker', 'compose', '-p', project, '-f', str(ROOT / 'docker/acquisition/compose.test.yaml')]
    results = {'project': project, 'cases': [], 'snapshot': str(args.snapshot), 'real_bundle': REAL_BUNDLE.name,
               'volumes': [env[k] for k in ('RJ_TEST_PROFILE_VOLUME', 'RJ_TEST_JOBS_VOLUME', 'RJ_TEST_STATE_VOLUME')]}

    def save():
        (out / 'results.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')

    def command(cmd, expect=0, name=None, timeout=900):
        result = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        combined = result.stdout + result.stderr
        assert token.encode() not in combined, 'Secret leaked in command output'
        if name:
            (out / (name + '.log')).write_bytes(combined)
            results['cases'].append({'case': name, 'exit': result.returncode, 'ok': result.returncode == expect})
            save()
        assert result.returncode == expect, (name, combined.decode(errors='replace')[-3000:])
        return result.stdout

    def ready():
        probe = "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8002/health',timeout=1).status==200"
        for _ in range(100):
            r = subprocess.run(base + ['exec', '-T', 'acquisition', 'python', '-c', probe], cwd=ROOT, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                return
            time.sleep(.2)
        raise RuntimeError('Acquisition not ready')

    results['images'] = {i: json.loads(command(['docker', 'image', 'inspect', i]))[0]['Id']
                         for i in (args.acquisition_image, args.backend_image)}
    results['snapshot_check'] = comprobar_copia(snapshot)
    command(['docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--cap-drop', 'ALL', '--security-opt',
             'no-new-privileges:true', '--entrypoint', 'python', args.acquisition_image, '-B', '-c',
             'from acquisition.service import configuration; configuration({})'],
            expect=1, name='service-disabled-without-provider')
    # Original responses of the real bundle (provenance.jsonl), byte for byte, as the routes of the local site.
    raw = command(['docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--cap-drop', 'ALL', '--mount',
                   f'type=bind,source={evidence},target=/evidence,readonly', '--entrypoint', 'python',
                   args.backend_image, '-B', '-c',
                   "import base64,json;e=json.load(open('/evidence/bundle.json'));rows=[json.loads(l) for l in "
                   "base64.b64decode(e['files']['provenance.jsonl']).splitlines()];"
                   "print(json.dumps({r['path']:r['body_b64'] for r in sorted(rows,key=lambda r:r['number'])}))"])
    replay = json.loads(raw)
    assert len(replay) == 5
    routes.write_text(json.dumps(replay), encoding='utf-8')
    results['replayed_routes'] = {p: len(base64.b64decode(b)) for p, b in replay.items()}
    for volume in results['volumes']:
        assert subprocess.run(['docker', 'volume', 'inspect', volume], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode != 0
        command(['docker', 'volume', 'create', '--label', 'rj.acquisition.test=' + project, volume])
    save()
    config = command(base + ['config', '--format', 'json'])
    parsed = json.loads(config)
    assert parsed['networks']['control']['internal'] is True
    assert parsed['services']['backend-client']['security_opt'] == ['no-new-privileges:true']
    (out / 'compose-config.json').write_bytes(config)
    try:
        command(base + ['run', '--rm', '--no-deps', 'profile-admin', 'initialize-profile', '--confirm-new-profile'],
                name='initialize-profile')
        command(base + ['run', '--rm', '--no-deps', 'backend-client', 'prepare'], name='prepare-isolated-copy')
        command(base + ['up', '-d', '--no-deps', 'acquisition'], name='start-acquisition')
        try:
            ready()
        except Exception:
            command(base + ['logs', '--no-color'], name='acquisition-startup-logs')
            raise
        inspect = json.loads(command(['docker', 'inspect', command(base + ['ps', '-q', 'acquisition']).decode().strip()]))[0]
        targets = sorted(m['Destination'] for m in inspect['Mounts'])
        assert targets == ['/acquisition-jobs', '/fixtures/routes.json', '/profiles', '/run/secrets/acquisition-token'], targets
        assert not inspect['HostConfig']['Privileged'] and inspect['HostConfig']['CapDrop'] == ['ALL']
        results['acquisition_mounts'] = targets
        (out / 'acquisition-inspect.json').write_text(json.dumps(inspect, indent=2), encoding='utf-8')
        command(base + ['exec', '-T', 'acquisition', 'python', '-B', '-m', 'unittest', 'test_protocol', 'test_transport'],
                name='contract-and-transport-tests')
        command(base + ['run', '--rm', '--no-deps', 'backend-client', 'auth-probe'], name='live-authentication')
        command(base + ['run', '--rm', '--no-deps', 'backend-client', 'real-bundle'], name='real-bundle-verification')
        command(base + ['run', '--rm', '--no-deps', 'backend-client', 'flow'], name='complete-flow-with-recovery')
        command(base + ['run', '--rm', '--no-deps', 'backend-client', 'restart-start'], name='job-before-crash')
        command(base + ['kill', '-s', 'SIGKILL', 'acquisition'], name='kill-acquisition-during-job')
        command(base + ['rm', '-f', 'acquisition'])
        command(base + ['run', '--rm', '--no-deps', 'profile-admin', 'verify-profile'], expect=74, name='profile-needs-recovery')
        command(base + ['run', '--rm', '--no-deps', 'profile-admin', 'recover-profile', '--confirm-service-stopped',
                        '--confirm-no-external-users'], name='manual-profile-recovery')
        command(base + ['up', '-d', '--no-deps', 'acquisition'], name='restart-acquisition')
        ready()
        command(base + ['run', '--rm', '--no-deps', 'backend-client', 'restart-check'], name='no-automatic-resume')
        env['RJ_TEST_SCENARIO'] = 'prohibido'
        command(base + ['up', '-d', '--no-deps', '--force-recreate', 'acquisition'], name='acquisition-refusing-site')
        ready()
        command(base + ['run', '--rm', '--no-deps', 'backend-client', 'refusal'], name='refusal-persisted-no-import')
        reports = command(base + ['run', '--rm', '--no-deps', '--entrypoint', 'python', 'backend-client', '-c',
                                  "import json,pathlib;print(json.dumps({p.name:json.loads(p.read_text()) for p in "
                                  "sorted(pathlib.Path('/state').glob('*-report.json'))}))"])
        results['reports'] = json.loads(reports)
        logs = command(base + ['logs', '--no-color'], name='service-logs-secret-audit')
        assert token.encode() not in logs
        command(base + ['down', '--remove-orphans'], name='stop-test-stack')
        results['ok'] = True
        save()
        print('PASS ' + str(out), flush=True)
    finally:
        cleanup = subprocess.run(base + ['down', '--remove-orphans'], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        assert token.encode() not in cleanup.stdout + cleanup.stderr
        (out / 'cleanup.log').write_bytes(cleanup.stdout + cleanup.stderr)
        secret.unlink(missing_ok=True)
        secret_dir.rmdir()
        tmp.cleanup()
        results['secret_removed'] = True
        save()


if __name__ == '__main__':
    main()
