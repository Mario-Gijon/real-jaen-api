"""Full update validation: Next (production build, host) → /api/v1/updates → backend in control mode → acquisition
service with its local provider, over a copy of an identified inactive snapshot. No external network for
acquisition; nothing reaches SofaScore.

python -X utf8 docker/acquisition/test_updates.py --acquisition-image IMG --backend-image IMG \
    --snapshot <copias/...sqlite> --evidence <new folder> \
    [--frontend ../rj-analyst]

The frontend needs a production build (npm run build). Screenshots (1440 and 390 px) and reports go to --evidence.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

from test_integration import extract_real_bundle

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--acquisition-image', required=True)
    ap.add_argument('--backend-image', required=True)
    ap.add_argument('--snapshot', type=Path, required=True)
    ap.add_argument('--evidence', type=Path, required=True)
    ap.add_argument('--frontend', type=Path, default=ROOT.parent / 'rj-analyst')
    ap.add_argument('--control-port', type=int, default=18001)
    ap.add_argument('--frontend-port', type=int, default=3310)
    a = ap.parse_args()
    if a.snapshot.name == 'real-jaen.sqlite' or a.snapshot.parent.name != 'copias':
        raise SystemExit('Only an identified inactive copy from copias/, never the active base')
    out = a.evidence.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'capturas').mkdir()
    project = 'rj-upd-test-' + uuid.uuid4().hex[:10]
    tokens = {name: secrets.token_hex(32) for name in ('acquisition', 'control', 'access', 'session')}
    files = {}
    for name, value in tokens.items():
        files[name] = out / f'{name}-secret'
        files[name].write_text(value, encoding='ascii')
    tmp = tempfile.TemporaryDirectory(prefix=project + '-')
    sys.path.insert(0, str(ROOT))
    from persistencia.sqlite import copia_consistente
    snapshot = Path(tmp.name) / 'inactive-snapshot.sqlite'
    copia_consistente(a.snapshot, snapshot)
    routes = Path(tmp.name) / 'routes.json'
    evidence = extract_real_bundle(Path(tmp.name) / 'evidence')
    env = os.environ.copy()
    env.update(RJ_TEST_ACQUISITION_IMAGE=a.acquisition_image, RJ_TEST_BACKEND_IMAGE=a.backend_image, RJ_TEST_MODE='1',
               RJ_TEST_SECRET_FILE=str(files['acquisition']), RJ_TEST_CONTROL_SECRET_FILE=str(files['control']),
               RJ_TEST_ROUTES=str(routes), RJ_TEST_SNAPSHOT=str(snapshot), RJ_TEST_CONTROL_PORT=str(a.control_port),
               RJ_TEST_PROFILE_VOLUME=project + '-profiles', RJ_TEST_JOBS_VOLUME=project + '-jobs',
               RJ_TEST_STATE_VOLUME=project + '-state', RJ_TEST_EVIDENCE_DIR=str(evidence))
    base = ['docker', 'compose', '-p', project, '-f', str(ROOT / 'docker/acquisition/compose.test.yaml'), '--profile', 'updates']
    results = dict(project=project, cases=[], volumes=[env[k] for k in ('RJ_TEST_PROFILE_VOLUME', 'RJ_TEST_JOBS_VOLUME', 'RJ_TEST_STATE_VOLUME')])
    control_url = f'http://127.0.0.1:{a.control_port}'
    front_url = f'http://127.0.0.1:{a.frontend_port}'

    def save():
        (out / 'results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    def leaked(blob):
        return [n for n, v in tokens.items() if v.encode() in blob]

    def command(cmd, expect=0, name=None, timeout=900, cwd=ROOT, extra_env=None):
        result = subprocess.run(cmd, cwd=cwd, env=dict(env, **(extra_env or {})), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        combined = result.stdout + result.stderr
        assert not leaked(combined), ('secret in output', name, leaked(combined))
        if name:
            (out / (name + '.log')).write_bytes(combined)
            results['cases'].append(dict(case=name, exit=result.returncode, ok=result.returncode == expect))
            save()
        assert result.returncode == expect, (name, combined.decode(errors='replace')[-3000:])
        return result.stdout

    def http(url, token=None):
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token} if token else {})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            body = error.read()
            error.close()
            return error.code, json.loads(body or b'null')

    def wait_http(url, attempts=150):
        for _ in range(attempts):
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(.4)
        raise RuntimeError('Not ready: ' + url)

    def acquisition_ready():
        probe = "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8002/health',timeout=1).status==200"
        for _ in range(100):
            if subprocess.run(base + ['exec', '-T', 'acquisition', 'python', '-c', probe], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                return
            time.sleep(.2)
        raise RuntimeError('Acquisition not ready')

    raw = command(['docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--cap-drop', 'ALL', '--mount',
                   f'type=bind,source={evidence},target=/evidence,readonly', '--entrypoint', 'python', a.backend_image, '-B', '-c',
                   "import base64,json;e=json.load(open('/evidence/bundle.json'));rows=[json.loads(l) for l in "
                   "base64.b64decode(e['files']['provenance.jsonl']).splitlines()];"
                   "print(json.dumps({r['path']:r['body_b64'] for r in sorted(rows,key=lambda r:r['number'])}))"])
    routes.write_text(raw.decode(), encoding='utf-8')
    for volume in results['volumes']:
        assert subprocess.run(['docker', 'volume', 'inspect', volume], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
        command(['docker', 'volume', 'create', '--label', 'rj.updates.test=' + project, volume])
    save()
    next_process = None
    try:
        command(base + ['run', '--rm', '--no-deps', 'profile-admin', 'initialize-profile', '--confirm-new-profile'], name='initialize-profile')
        command(base + ['run', '--rm', '--no-deps', 'backend-client', 'prepare'], name='prepare-isolated-copy')
        command(base + ['up', '-d', '--no-deps', 'acquisition', 'backend-control'], name='start-acquisition-and-control')
        acquisition_ready()
        wait_http(control_url + '/health')
        status, body = http(control_url + '/internal/v1/updates/current')
        assert status == 401, (status, body)
        status, body = http(control_url + '/internal/v1/updates/current', tokens['control'])
        assert status == 200 and body['scope']['kind'] == 'pilot' and body['last_published'] is None, body
        results['initial'] = body
        node_env = dict(env, DATA_MODE='real', LOCAL_DATA_API_URL=control_url, RJ_ACCESS_PASSWORD_FILE=str(files['access']),
                        RJ_SESSION_SECRET_FILE=str(files['session']),
                        RJ_CONTROL_SECRET_FILE=str(files['control']), NODE_ENV='production')
        log = (out / 'next-server.log').open('wb')
        next_process = subprocess.Popen(['node', 'node_modules/next/dist/bin/next', 'start', '-H', '127.0.0.1', '-p', str(a.frontend_port)],
                                        cwd=a.frontend, env=node_env, stdout=log, stderr=subprocess.STDOUT)
        wait_http(front_url + '/api/v1/session')

        def browser(phase):
            raw = command(['node', 'scripts/validate-updates-stack.mjs', phase, front_url, str(files['access']), str(out / 'capturas'),
                           str(files['control']), str(files['acquisition'])], name='browser-' + phase, cwd=a.frontend, timeout=900)
            report = json.loads(raw.decode().strip().splitlines()[-1])
            results['browser-' + phase] = report
            save()
            assert report['ok'], report
            return report

        publish = browser('publish')
        assert next(s for s in publish['steps'] if s['name'] == 'cancelled')['unchanged'] is True
        assert next(s for s in publish['steps'] if s['name'] == 'published')['postsForThisStart'] == 1
        status, after_publish = http(control_url + '/internal/v1/updates/current', tokens['control'])
        assert after_publish['last_job']['status'] == 'completed' and after_publish['last_published'], after_publish
        results['after_publish'] = after_publish
        # SIGKILL of the backend while a job acquires: after the restart the job is interrupted, never resumed, and
        # the published generation is the same one.
        _, match_before = http(control_url + '/api/v1/matches/16599363')
        request = urllib.request.Request(control_url + '/internal/v1/updates', data=b'', method='POST',
                                         headers={'Authorization': 'Bearer ' + tokens['control'], 'Idempotency-Key': str(uuid.uuid4())})
        with urllib.request.urlopen(request, timeout=10) as response:
            crashed = json.loads(response.read())['job']
        for _ in range(150):
            if http(control_url + f"/internal/v1/updates/{crashed['id']}", tokens['control'])[1]['phase'] == 'acquiring':
                break
            time.sleep(.2)
        command(base + ['kill', '-s', 'SIGKILL', 'backend-control'], name='kill-control-during-job')
        command(base + ['up', '-d', '--no-deps', 'backend-control'], name='restart-control-after-kill')
        wait_http(control_url + '/health')
        status, after_kill = http(control_url + '/internal/v1/updates/current', tokens['control'])
        assert after_kill['active_job'] is None and after_kill['last_job']['id'] == crashed['id'], after_kill
        assert after_kill['last_job']['error']['code'] == 'INTERRUPTED', after_kill
        assert after_kill['last_published'] == after_publish['last_published'], after_kill
        assert http(control_url + '/api/v1/matches/16599363')[1] == match_before
        results['after_kill'] = after_kill
        # The acquisition job of the killed request finishes on its own (its bundle is never imported); wait for it so
        # the profile session is closed before the acquisition service is recreated.
        probe = ("import sqlite3;print(sqlite3.connect('/acquisition-jobs/jobs.sqlite').execute(\"SELECT count(*) FROM job "
                 "WHERE state IN ('queued','running','cancelling')\").fetchone()[0])")
        for _ in range(300):
            if command(base + ['exec', '-T', 'acquisition', 'python', '-c', probe]).strip() == b'0':
                break
            time.sleep(1)
        else:
            raise RuntimeError('Acquisition job did not finish')
        env['RJ_TEST_SCENARIO'] = 'prohibido'
        command(base + ['up', '-d', '--no-deps', '--force-recreate', 'acquisition'], name='acquisition-refusing-site')
        acquisition_ready()
        refusal = browser('refusal')
        assert next(s for s in refusal['steps'] if s['name'] == 'refused')['unchanged'] is True
        status, after_refusal = http(control_url + '/internal/v1/updates/current', tokens['control'])
        assert after_refusal['last_job']['error']['code'] == 'ACQUISITION_FAILED', after_refusal
        assert after_refusal['last_published'] == after_publish['last_published'], after_refusal
        results['after_refusal'] = after_refusal
        # Restart of the backend: jobs and the published generation persist, nothing is resumed.
        command(base + ['restart', 'backend-control'], name='restart-control')
        wait_http(control_url + '/health')
        status, restarted = http(control_url + '/internal/v1/updates/current', tokens['control'])
        assert restarted['last_published'] == after_publish['last_published'] and restarted['active_job'] is None, restarted
        results['after_restart'] = restarted
        # The access session: it survives restarting the frontend (its key comes from the secret files), and rotating
        # the session secret in place revokes the cookie issued before.
        def front(path, method='GET', cookie=None, body=None):
            headers = {'Origin': front_url, **({'Cookie': cookie} if cookie else {}),
                       **({'Content-Type': 'application/json'} if body is not None else {})}
            req = urllib.request.Request(front_url + path, method=method, headers=headers,
                                         data=json.dumps(body).encode() if body is not None else None)
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    return response.status, response.headers, response.read()
            except urllib.error.HTTPError as error:
                payload = error.read()
                error.close()
                return error.code, error.headers, payload

        status, headers, _ = front('/api/v1/session', 'POST', body={'password': tokens['access']})
        assert status == 204, status
        session_cookie = headers['Set-Cookie'].split(';')[0]
        assert 'Max-Age=7776000' in headers['Set-Cookie'] and 'HttpOnly' in headers['Set-Cookie'], headers['Set-Cookie']
        assert front('/api/v1/source')[0] == 401
        assert front('/api/v1/source', cookie=session_cookie)[0] == 200
        next_process.terminate()
        next_process.wait(30)
        log = (out / 'next-server.log').open('ab')
        next_process = subprocess.Popen(['node', 'node_modules/next/dist/bin/next', 'start', '-H', '127.0.0.1', '-p', str(a.frontend_port)],
                                        cwd=a.frontend, env=node_env, stdout=log, stderr=subprocess.STDOUT)
        wait_http(front_url + '/api/v1/session')
        survived = front('/api/v1/source', cookie=session_cookie)[0]
        signed_in = json.loads(front('/api/v1/session', cookie=session_cookie)[2])['signedIn']
        assert (survived, signed_in) == (200, True), (survived, signed_in)
        tokens['session'] = secrets.token_hex(32)
        files['session'].write_text(tokens['session'], encoding='ascii')  # in place: same file, new content
        revoked = front('/api/v1/source', cookie=session_cookie)[0]
        assert revoked == 401, revoked
        status, headers, _ = front('/api/v1/session', 'POST', body={'password': tokens['access']})
        assert status == 204 and front('/api/v1/source', cookie=headers['Set-Cookie'].split(';')[0])[0] == 200
        results['access-session'] = dict(survives_frontend_restart=True, revoked_by_rotation=True, new_session_after_rotation=True)
        save()
        logs = command(base + ['logs', '--no-color'], name='service-logs-secret-audit')
        assert not leaked(logs)
        results['ok'] = True
        save()
        print('PASS ' + str(out), flush=True)
    finally:
        if next_process is not None:
            next_process.terminate()
            next_process.wait(30)
            assert not leaked((out / 'next-server.log').read_bytes()), 'secret in Next log'
        cleanup = subprocess.run(base + ['down', '--remove-orphans'], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (out / 'cleanup.log').write_bytes(cleanup.stdout + cleanup.stderr)
        for file in files.values():
            file.unlink(missing_ok=True)
        tmp.cleanup()
        results['secrets_removed'] = True
        save()


if __name__ == '__main__':
    main()
