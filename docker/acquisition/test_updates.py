"""FastAPI update validation: Next → sports-data → acquisition fixtures over an isolated SQLite copy.

No provider or external-network access is used. The local frontend requires a production build.

python -X utf8 docker/acquisition/test_updates.py --acquisition-image IMG --backend-image IMG \
    --fastapi-image IMG --snapshot <copias/...sqlite> --evidence <new folder> [--frontend ../dashboard]

Screenshots (1440 and 390 px) and reports go to --evidence.
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
    ap.add_argument('--fastapi-image', required=True, help='Imagen sports-data construida desde Dockerfile')
    ap.add_argument('--frontend', type=Path, default=ROOT.parent / 'dashboard')
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
    secret_dir = out / '.secrets'
    secret_dir.mkdir(mode=0o700)
    for name, value in tokens.items():
        files[name] = secret_dir / f'{name}-secret'
        files[name].write_text(value, encoding='ascii')
        # Docker's non-root UID 10001 needs read access through the read-only bind mount.
        files[name].chmod(0o644)
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
               RJ_TEST_ROUTES=str(routes), RJ_TEST_SNAPSHOT=str(snapshot),
               RJ_TEST_FASTAPI_IMAGE=a.fastapi_image,
               RJ_TEST_PROFILE_VOLUME=project + '-profiles', RJ_TEST_JOBS_VOLUME=project + '-jobs',
               RJ_TEST_STATE_VOLUME=project + '-state', RJ_TEST_EVIDENCE_DIR=str(evidence))
    update_service = 'backend-control'
    update_profile = 'updates'
    base = ['docker', 'compose', '-p', project, '-f', str(ROOT / 'docker/acquisition/compose.test.yaml'),
            '--profile', update_profile]
    results = dict(project=project, cases=[],
                   volumes=[env[k] for k in ('RJ_TEST_PROFILE_VOLUME', 'RJ_TEST_JOBS_VOLUME', 'RJ_TEST_STATE_VOLUME')])
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

    def http(url, token=None, *, method='GET', headers=None, body=None):
        request_headers = dict(headers or {})
        if token:
            request_headers['Authorization'] = 'Bearer ' + token
        data = body.encode('utf-8') if body is not None else (b'' if method == 'POST' else None)
        request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
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
                status, _ = http(url)
                if status == 200:
                    return
            except (OSError, urllib.error.URLError, subprocess.SubprocessError):
                pass
            time.sleep(.4)
        raise RuntimeError('Not ready: ' + url)

    def container_config(service):
        ident = command(base + ['ps', '-q', service]).decode().strip()
        assert ident, ('missing container', service)
        return json.loads(command(['docker', 'inspect', ident]))[0]

    def coordinator_generation():
        probe = (
            "import json,sqlite3;c=sqlite3.connect('file:/state/base.sqlite.actualizaciones/jobs.sqlite?mode=ro',uri=True);"
            "r=c.execute('SELECT detalle FROM confirmado WHERE id=1').fetchone();"
            "assert r;print(json.loads(r[0])['generacion']);c.close()"
        )
        ident = container_config(update_service)['Id']
        return command(['docker', 'exec', ident, 'python', '-B', '-c', probe]).decode().strip()

    def wait_docker_healthy(service, attempts=90):
        for _ in range(attempts):
            inspected = container_config(service)
            health = inspected['State'].get('Health')
            if health and health['Status'] == 'healthy':
                return inspected
            if health and health['Status'] == 'unhealthy':
                raise RuntimeError(service + ' Docker healthcheck failed: ' + json.dumps(health.get('Log', [])[-3:]))
            time.sleep(1)
        raise RuntimeError(service + ' Docker healthcheck did not become healthy')

    def wait_job(ident, terminal=('completed', 'failed', 'cancelled'), attempts=1200):
        for _ in range(attempts):
            status, item = http(control_url + '/internal/v1/updates/' + ident, tokens['control'])
            if status == 200 and item.get('status') in terminal:
                return item
            time.sleep(.25)
        raise RuntimeError('Update job did not finish: ' + ident)

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
        config = json.loads(command(base + ['config', '--format', 'json']))
        assert config['networks']['control']['internal'] is True
        command(base + ['up', '-d', '--no-deps', 'acquisition', update_service],
                name='start-acquisition-and-fastapi')
        acquisition_ready()
        service_inspect = wait_docker_healthy(update_service)
        acquisition_inspect = wait_docker_healthy('acquisition')
        control_network = project + '_control'
        fastapi_networks = service_inspect['NetworkSettings']['Networks']
        acquisition_networks = acquisition_inspect['NetworkSettings']['Networks']
        assert set(fastapi_networks) == {control_network}, fastapi_networks
        assert set(acquisition_networks) == {control_network}, acquisition_networks
        assert fastapi_networks[control_network]['NetworkID'] == acquisition_networks[control_network]['NetworkID']
        control_ip = fastapi_networks[control_network]['IPAddress']
        assert control_ip, fastapi_networks[control_network]
        control_url = f'http://{control_ip}:8002'
        wait_http(control_url + '/health')
        allowlist_probe = (
            "from persistencia.adquisicion import productores_aprobados;"
            "approved=productores_aprobados();assert approved;print('producer_allowlist=present')"
        )
        allowlist_output = command(['docker', 'exec', service_inspect['Id'], 'python', '-B', '-c', allowlist_probe],
                                   name='verify-fastapi-producer-allowlist')
        assert b'producer_allowlist=present' in allowlist_output
        targets = sorted(m['Destination'] for m in service_inspect['Mounts'])
        assert targets == ['/run/secrets/acquisition-token', '/run/secrets/control-token', '/state'], targets
        assert not service_inspect['HostConfig']['Privileged'] and service_inspect['HostConfig']['CapDrop'] == ['ALL']
        assert service_inspect['Config']['Env'] and 'SPORTS_DATA_CONTROL=1' in service_inspect['Config']['Env']
        assert '/state' not in [m['Destination'] for m in acquisition_inspect['Mounts']]
        bindings = service_inspect['NetworkSettings']['Ports'].get('8002/tcp') or []
        assert not bindings, bindings
        results['container_isolation'] = dict(fastapi_mounts=targets,
                                              acquisition_mounts=sorted(m['Destination'] for m in acquisition_inspect['Mounts']),
                                              no_shared_sports_sqlite=True, networks='same-private-internal-network',
                                              published_ports=bindings, control_address='private-network-ip',
                                              fastapi_health=service_inspect['State']['Health']['Status'],
                                              acquisition_health=acquisition_inspect['State']['Health']['Status'])
        results['images'] = {image: json.loads(command(['docker', 'image', 'inspect', image]))[0]['Id']
                             for image in (a.fastapi_image, a.acquisition_image, a.backend_image)}
        initial_status, scope = http(control_url + '/api/v1/scope')
        assert initial_status == 200 and isinstance(scope, dict), (initial_status, scope)
        initial_status, initial_match = http(control_url + '/api/v1/matches/16599363')
        assert initial_status == 200 and isinstance(initial_match, dict), (initial_status, initial_match)
        status, body = http(control_url + '/internal/v1/updates/current')
        assert status == 401, (status, body)
        status, body = http(control_url + '/internal/v1/updates/current', tokens['control'])
        assert status == 200 and body['scope']['kind'] == 'pilot' and body['last_published'] is None, body
        results['initial'] = body
        node_env = dict(env, DATA_MODE='real', LOCAL_DATA_API_URL=control_url, RJ_ACCESS_PASSWORD_FILE=str(files['access']),
                        RJ_SESSION_SECRET_FILE=str(files['session']), RJ_CONTROL_SECRET_FILE=str(files['control']),
                        NODE_ENV='production')
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
        status, browser_publish = http(control_url + '/internal/v1/updates/current', tokens['control'])
        assert status == 200 and browser_publish['last_job']['status'] == 'completed' and browser_publish['last_published'], browser_publish
        results['dashboard_publish'] = browser_publish
        # Exercise actual HTTP between both containers. The request executor runs inside sports-data and reads
        # its mounted control token, while acquisition serves only the local recorded fixture.
        container_before = service_inspect['Id']
        initial_generation = coordinator_generation()
        _, match_before = http(control_url + '/api/v1/matches/16599363')

        cancel_key = str(uuid.uuid4())
        cancel_status, cancel_started = http(control_url + '/internal/v1/updates', tokens['control'], method='POST',
                                             headers={'Idempotency-Key': cancel_key})
        assert cancel_status == 202 and cancel_started['job']['id'], cancel_started
        cancel_id = cancel_started['job']['id']
        for _ in range(120):
            _, active = http(control_url + '/internal/v1/updates/' + cancel_id, tokens['control'])
            if active['phase'] == 'acquiring':
                break
            time.sleep(.15)
        else:
            raise RuntimeError('FastAPI job did not reach acquisition phase for cancellation')
        cancel_status, cancel_body = http(control_url + '/internal/v1/updates/' + cancel_id + '/cancel',
                                          tokens['control'], method='POST')
        assert cancel_status == 202, (cancel_status, cancel_body)
        cancelled = wait_job(cancel_id)
        assert (cancelled['status'], cancelled['error']['code'], cancelled['published']) == \
            ('cancelled', 'CANCELLED', False), cancelled
        assert http(control_url + '/api/v1/matches/16599363')[1] == match_before
        results['cancelled'] = dict(status=cancelled['status'], old_query_unchanged=True)

        idempotency_key = str(uuid.uuid4())
        headers = {'Idempotency-Key': idempotency_key}
        first_status, first = http(control_url + '/internal/v1/updates', tokens['control'], method='POST', headers=headers)
        second_status, second = http(control_url + '/internal/v1/updates', tokens['control'], method='POST', headers=headers)
        assert first_status in (200, 202) and second_status in (200, 202), (first_status, second_status)
        assert first['job']['id'] == second['job']['id'] and second['reused'] is True, (first, second)
        published_job = wait_job(first['job']['id'])
        if published_job['status'] != 'completed':
            results['publication_failure'] = published_job
            save()
            diagnostic = command(base + ['logs', '--no-color'], name='publication-failure-logs')
            raise AssertionError('FastAPI publication failed: ' + json.dumps(published_job) + '\n' +
                                 diagnostic.decode(errors='replace')[-5000:])
        assert (published_job['status'], published_job['published']) == ('completed', True), published_job
        status, after_publish = http(control_url + '/internal/v1/updates/current', tokens['control'])
        assert status == 200 and after_publish['last_job']['id'] == published_job['id']
        published_generation = coordinator_generation()
        assert published_generation != initial_generation
        assert after_publish['last_published'], after_publish
        query_status, match_after = http(control_url + '/api/v1/matches/16599363')
        assert query_status == 200 and isinstance(match_after, dict), (query_status, match_after)
        service_after_publish = container_config(update_service)
        assert service_after_publish['Id'] == container_before, 'FastAPI container restarted during publication'
        assert service_after_publish['State']['Status'] == 'running'
        results['after_publish'] = dict(state=after_publish, generation_changed=True, query_status=query_status,
                                        container_unchanged=True, idem_reused=second['reused'])
        published_value = after_publish['last_published']
        # SIGKILL of the backend while a job acquires: after the restart the job is interrupted, never resumed, and
        # the published generation is the same one.
        _, match_before = http(control_url + '/api/v1/matches/16599363')
        crash_status, crash_response = http(control_url + '/internal/v1/updates', tokens['control'], method='POST',
                                            headers={'Idempotency-Key': str(uuid.uuid4())})
        assert crash_status == 202, (crash_status, crash_response)
        crashed = crash_response['job']
        for _ in range(150):
            if http(control_url + f"/internal/v1/updates/{crashed['id']}", tokens['control'])[1]['phase'] == 'acquiring':
                break
            time.sleep(.2)
        else:
            raise RuntimeError('Update job did not reach acquisition phase before process kill')
        command(base + ['kill', '-s', 'SIGKILL', update_service], name='kill-backend-during-job')
        command(base + ['up', '-d', '--no-deps', update_service], name='restart-backend-after-kill')
        wait_http(control_url + '/health')
        status, after_kill = http(control_url + '/internal/v1/updates/current', tokens['control'])
        assert after_kill['active_job'] is None and after_kill['last_job']['id'] == crashed['id'], after_kill
        assert after_kill['last_job']['error']['code'] == 'INTERRUPTED', after_kill
        assert after_kill['last_published'] == published_value, after_kill
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

        # Simulate acquisition being unavailable without changing its API or touching the sports-data volume.
        command(base + ['stop', 'acquisition'], name='stop-acquisition-for-unavailable-case')
        unavailable_status, unavailable_start = http(control_url + '/internal/v1/updates', tokens['control'],
                                                     method='POST', headers={'Idempotency-Key': str(uuid.uuid4())})
        assert unavailable_status == 202, (unavailable_status, unavailable_start)
        unavailable = wait_job(unavailable_start['job']['id'])
        assert unavailable['error']['code'] == 'ACQUISITION_UNAVAILABLE', unavailable
        status, after_unavailable = http(control_url + '/internal/v1/updates/current', tokens['control'])
        assert status == 200 and after_unavailable['last_published'] == published_value, \
            after_unavailable
        results['acquisition_unavailable'] = dict(status=unavailable['status'],
                                                  error=unavailable['error']['code'],
                                                  generation_preserved=True)
        command(base + ['start', 'acquisition'], name='restart-acquisition-after-unavailable-case')
        acquisition_ready()
        wait_docker_healthy('acquisition')

        env['RJ_TEST_SCENARIO'] = 'prohibido'
        command(base + ['up', '-d', '--no-deps', '--force-recreate', 'acquisition'], name='acquisition-refusing-site')
        acquisition_ready()
        refusal = browser('refusal')
        assert next(s for s in refusal['steps'] if s['name'] == 'refused')['unchanged'] is True
        status, after_refusal = http(control_url + '/internal/v1/updates/current', tokens['control'])
        assert after_refusal['last_job']['error']['code'] == 'ACQUISITION_FAILED', after_refusal
        assert after_refusal['last_published'] == published_value, after_refusal
        results['after_refusal'] = after_refusal
        # Restart of the backend: jobs and the published generation persist, nothing is resumed.
        command(base + ['restart', update_service], name='restart-backend')
        wait_http(control_url + '/health')
        status, restarted = http(control_url + '/internal/v1/updates/current', tokens['control'])
        assert restarted['last_published'] == published_value and restarted['active_job'] is None, restarted
        results['after_restart'] = restarted
        # The access session survives a frontend restart, while rotating its secret revokes the old cookie.
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
        files['session'].write_text(tokens['session'], encoding='ascii')
        revoked = front('/api/v1/source', cookie=session_cookie)[0]
        assert revoked == 401, revoked
        status, headers, _ = front('/api/v1/session', 'POST', body={'password': tokens['access']})
        assert status == 204 and front('/api/v1/source', cookie=headers['Set-Cookie'].split(';')[0])[0] == 200
        results['access-session'] = dict(survives_frontend_restart=True, revoked_by_rotation=True,
                                         new_session_after_rotation=True)
        save()

        # Stopping the process must release its SQLite coordinator lock and leave both databases readable.
        command(base + ['stop', update_service], name='stop-fastapi-and-release-sqlite-lock')
        lock_probe = '''
import json
import sqlite3
from pathlib import Path
from persistencia.bloqueo import bloquear

base = Path('/state/base.sqlite')
c = sqlite3.connect('file:/state/base.sqlite?mode=ro', uri=True)
integrity = c.execute('PRAGMA integrity_check').fetchone()[0]
fk = c.execute('PRAGMA foreign_key_check').fetchall()
c.close()
j = sqlite3.connect('file:/state/base.sqlite.actualizaciones/jobs.sqlite?mode=ro', uri=True)
journal = j.execute('PRAGMA integrity_check').fetchone()[0]
j.close()
assert integrity == 'ok' and not fk and journal == 'ok', (integrity, fk, journal)
with bloquear(base):
    print(json.dumps({'integrity': integrity, 'foreign_key_errors': fk, 'journal': journal,
                      'lock_released': True}))
'''
        check = command(['docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--cap-drop', 'ALL',
                         '--mount', f'type=volume,source={env["RJ_TEST_STATE_VOLUME"]},target=/state',
                         '--entrypoint', 'python', a.fastapi_image, '-B', '-c', lock_probe],
                        name='verify-sqlite-integrity-and-released-lock')
        results['shutdown'] = json.loads(check.decode().strip().splitlines()[-1])
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
        secret_dir.rmdir()
        tmp.cleanup()
        results['secrets_removed'] = True
        save()


if __name__ == '__main__':
    main()
