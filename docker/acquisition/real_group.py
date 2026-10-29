"""Single real run of the Group 2 recipe on an isolated copy, and its rehearsals, through the button's path.

    python -X utf8 docker/acquisition/real_group.py MODE --snapshot <copias/...sqlite> --snapshot-sha256 HEX \\
        --acquisition-image ID --backend-image ID --backend-client-image ID --evidence <new folder> [options]

Modes:
  rehearsal  Local provider (test image of the same tree, --acquisition-image), simulated time, no egress.
             --scenario ok | budget | refusal.
  offline    Definitive images with the real provider and NO egress: every send fails, nothing is published.
  real       Definitive images with egress. Requires --confirm-real-run, runs once (ledger) and only before
             --not-after. Contacts SofaScore.

Every mode: a new Compose project and new labelled volumes; the copy checked by name and sha256 (never the active
base); reduced limits (--max-attempts, --max-sends, never above production) and the restriction of details to
--detail-rounds, declared by the producer and required by the backend; one POST through backend-control; reports
taken while the service is idle, then every service stopped; integrity, identities and the sealed bundle checked
again with the backend stopped; the active base and the running containers compared before and after. Volumes
are kept. Prints one JSON line (results.json in the evidence folder).
"""
import argparse
import base64
import copy
from datetime import datetime, timezone
import hashlib
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

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RECIPE = 'sofascore-group2-2627-v1'
SECTIONS = ('lineups', 'statistics', 'incidents')
LEDGER_DIR = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'rj-acquisition' / 'real-runs'
ACTIVE_BASE = Path('C:/Users/broke/dev/datos-importados/base/real-jaen.sqlite')


class Refused(SystemExit):
    """A guard before anything is created or sent."""


def iso(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def ledger_path(rounds):
    return LEDGER_DIR / f'{RECIPE}-round{"-".join(map(str, rounds))}.started'


def preflight(a, now):
    """Guards that need no Docker resource: returns the facts the run records. Raises Refused."""
    snap = a.snapshot.resolve()
    if snap.name == 'real-jaen.sqlite' or snap.parent.name != 'copias' or not snap.is_file():
        raise Refused('Only an identified inactive copy from copias/, never the active base')
    if snap == ACTIVE_BASE.resolve():
        raise Refused('The active base is never used')
    digest = sha256_file(snap)
    if digest != a.snapshot_sha256:
        raise Refused(f'Copy with another sha256: {digest}')
    if not 1 <= a.max_attempts <= a.max_sends <= 120 or a.max_attempts > 116:
        raise Refused('Limits can only lower the production maxima (116 attempts, 120 sends)')
    rounds = sorted(set(a.detail_rounds))
    if not rounds or not all(1 <= r <= 38 for r in rounds):
        raise Refused('Declare the rounds whose matches may be consulted')
    if a.evidence.exists():
        raise Refused('The evidence folder must be new')
    facts = dict(snapshot=str(snap), snapshot_sha256=digest, detail_rounds=rounds, ledger=None)
    if a.mode == 'real':
        if not a.confirm_real_run:
            raise Refused('The real run requires --confirm-real-run')
        if now >= datetime.fromisoformat(a.not_after).timestamp():
            raise Refused(f'Outside the window of this run (not after {a.not_after})')
        ledger = ledger_path(rounds)
        if ledger.exists():
            raise Refused(f'This real run already started once ({ledger}); it is never repeated')
        facts['ledger'] = str(ledger)
    return facts


def rounds_of(snapshot):
    """{round: [current match ids]} of the identified copy, read on a temporary copy (no Docker, no network)."""
    sys.path.insert(0, str(ROOT))
    from persistencia.sqlite import copia_consistente
    from persistencia.actualizacion import preparar
    from persistencia.bundle import inventory
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / 'snapshot.sqlite'
        copia_consistente(snapshot, work)
        generation = preparar(work, ROOT / 'ambito.json', 'world')
        current = {e['id'] for e in inventory(generation)['events'] if e['current']}
        return {n: sorted(m['match_id'] for m in generation._datos['partidos'] if m['round'] == n and m['match_id'] in current)
                for n in range(1, 39)}


def rehearsal_routes(snapshot, scenario, now):
    """{path: bytes} of the local site: the season from the copy's own originals; round 9 finished (sections copied
    from round 8: test data of this isolated copy); a calendar change in round 10 and another in a far round (both
    must stay outside the details); in 'refusal', one round 9 event answers 500 to the browser and a 403 without
    challenge to the retry."""
    sys.path.insert(0, str(ROOT))
    from persistencia.sqlite import copia_consistente
    from persistencia.actualizacion import preparar
    from persistencia.bundle import inventory
    from acquisition.group_plan import rounds_to_list
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / 'snapshot.sqlite'
        copia_consistente(snapshot, work)
        generation = preparar(work, ROOT / 'ambito.json', 'world')
        baseline = inventory(generation)
        originals = {(i, k): v['registro'] for (i, k), v in generation._datos['originales'].items()}
    by_round = {}
    for e in baseline['events']:
        payload = (originals.get((e['id'], 'event')) or {}).get('payload')
        if e['current'] and payload:
            by_round.setdefault(payload['roundInfo']['round'], []).append(payload)
    nine = sorted(by_round[9], key=lambda p: p['startTimestamp'])
    donors = sorted(by_round[8], key=lambda p: p['id'])
    near, far = rounds_to_list(baseline, now)
    ten = sorted(by_round[10], key=lambda p: p['id'])[0]
    far_match = sorted(by_round[far[0]], key=lambda p: p['id'])[0]
    events = {p['id']: copy.deepcopy(p) for ps in by_round.values() for p in ps}
    for k, p in enumerate(nine):
        e = events[p['id']]
        e['status'] = dict(code=100, description='Ended', type='finished')
        e['homeScore'], e['awayScore'] = dict(current=k % 3, display=k % 3), dict(current=1, display=1)
    events[ten['id']]['startTimestamp'] += 3600
    events[far_match['id']]['startTimestamp'] += 86400
    routes = {}
    for number, ps in by_round.items():
        page = [events[p['id']] for p in ps]
        routes[f'/unique-tournament/17073/season/97382/events/round/{number}'] = json.dumps({'events': page}).encode()
    for ident, e in events.items():
        routes[f'/event/{ident}'] = json.dumps({'event': e}).encode()
    for k, p in enumerate(nine):
        donor = donors[k % len(donors)]['id']
        for key in SECTIONS:
            routes[f'/event/{p["id"]}/{key}'] = json.dumps(originals[(donor, key)]['payload']).encode()
    if scenario == 'refusal':
        routes[f'/event/{nine[2]["id"]}'] = b'__500_403__'
    expected = dict(round9=[p['id'] for p in nine], restricted=[ten['id'], far_match['id']], near=near, far=far,
                    refused=nine[2]['id'] if scenario == 'refusal' else None)
    return routes, expected


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mode', choices=('rehearsal', 'offline', 'real'))
    ap.add_argument('--snapshot', type=Path, required=True)
    ap.add_argument('--snapshot-sha256', required=True)
    ap.add_argument('--acquisition-image', required=True, help='image id: the test image of the tree for rehearsal')
    ap.add_argument('--backend-image', required=True)
    ap.add_argument('--backend-client-image', required=True)
    ap.add_argument('--evidence', type=Path, required=True)
    ap.add_argument('--max-attempts', type=int, default=60)
    ap.add_argument('--max-sends', type=int, default=70)
    ap.add_argument('--detail-rounds', type=int, nargs='+', default=[9])
    ap.add_argument('--scenario', choices=('ok', 'budget', 'refusal'), default='ok')
    ap.add_argument('--test-now', default='2026-10-30T12:00:00+00:00', help='simulated instant (rehearsal only)')
    ap.add_argument('--not-after', default='2026-10-30T19:00:00+00:00', help='end of the window of the real run')
    ap.add_argument('--confirm-real-run', action='store_true')
    ap.add_argument('--control-port', type=int, default=18003)
    ap.add_argument('--preflight-only', action='store_true', help='run the guards and stop before creating anything')
    a = ap.parse_args(argv)
    if a.mode != 'rehearsal' and a.scenario != 'ok':
        raise Refused('Scenarios exist only in rehearsal')
    started = time.time()
    facts = preflight(a, started)
    images = {}
    for name in ('acquisition_image', 'backend_image', 'backend_client_image'):
        r = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', getattr(a, name)], capture_output=True, text=True)
        if r.returncode != 0:
            raise Refused(f'Image not found: {getattr(a, name)}')
        images[name] = r.stdout.strip()
    if a.preflight_only:
        print(json.dumps(dict(preflight='ok', mode=a.mode, images=images, **facts)))
        return 0

    out = a.evidence.resolve()
    out.mkdir(parents=True)
    project = f'rj-real-group-{a.mode}-' + uuid.uuid4().hex[:8]
    results = dict(mode=a.mode, scenario=a.scenario, project=project, started_at=iso(started), images=images,
                   limits=dict(attempts=a.max_attempts, sends=a.max_sends), cases=[], **facts)
    tokens = {name: secrets.token_hex(32) for name in ('acquisition', 'control')}
    files = {}
    for name, value in tokens.items():
        files[name] = out / f'{name}-secret'
        files[name].write_text(value, encoding='ascii')
    tmp = tempfile.TemporaryDirectory(prefix=project + '-')
    provider = 'fixture-site' if a.mode == 'rehearsal' else 'sofascore'
    env = os.environ.copy()
    env.update(RJ_REAL_ACQUISITION_IMAGE=images['acquisition_image'], RJ_REAL_BACKEND_IMAGE=images['backend_image'],
               RJ_REAL_BACKEND_CLIENT_IMAGE=images['backend_client_image'], RJ_REAL_SECRET_FILE=str(files['acquisition']),
               RJ_REAL_CONTROL_SECRET_FILE=str(files['control']), RJ_REAL_SNAPSHOT=facts['snapshot'],
               RJ_REAL_SNAPSHOT_SHA256=facts['snapshot_sha256'], RJ_REAL_PROVIDER=provider,
               RJ_REAL_MAX_ATTEMPTS=str(a.max_attempts), RJ_REAL_MAX_SENDS=str(a.max_sends),
               RJ_REAL_DETAIL_ROUNDS=','.join(map(str, facts['detail_rounds'])), RJ_REAL_CONTROL_PORT=str(a.control_port),
               RJ_REAL_PROFILE_VOLUME=project + '-profiles', RJ_REAL_JOBS_VOLUME=project + '-jobs',
               RJ_REAL_STATE_VOLUME=project + '-state')
    compose = ['-f', str(HERE / 'compose.real-test.yaml')]
    if a.mode == 'rehearsal':
        routes, expected = rehearsal_routes(Path(facts['snapshot']), a.scenario, datetime.fromisoformat(a.test_now).timestamp())
        route_file = Path(tmp.name) / 'routes.json'
        route_file.write_text(json.dumps({p: base64.b64encode(b).decode() for p, b in routes.items()}), encoding='utf-8')
        env.update(RJ_REAL_ROUTES=str(route_file), RJ_REAL_TEST_NOW=a.test_now)
        compose += ['-f', str(HERE / 'compose.real-test.fixture.yaml')]
        results['expected'] = expected
    else:
        env.update(RJ_REAL_ENABLE='1')
        compose += ['-f', str(HERE / 'compose.real-test.sofascore.yaml')]
        if a.mode == 'real':
            compose += ['-f', str(HERE / 'compose.real-test.egress.yaml')]
    base = ['docker', 'compose', '-p', project, *compose]
    control_url = f'http://127.0.0.1:{a.control_port}'

    def save():
        (out / 'results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    def leaked(raw):
        return any(v.encode() in raw for v in tokens.values())

    def command(cmd, name, expect=0, timeout=900):
        r = subprocess.run(cmd, cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        combined = r.stdout + r.stderr
        (out / (name + '.log')).write_bytes(combined)
        results['cases'].append(dict(case=name, exit=r.returncode, ok=r.returncode == expect, secret_in_output=leaked(combined)))
        save()
        assert not leaked(combined), ('secret in output', name)
        assert r.returncode == expect, (name, combined.decode(errors='replace')[-3000:])
        return r.stdout

    def http(path, method='GET', headers=None):
        h = dict(headers or {}, Authorization='Bearer ' + tokens['control'])
        req = urllib.request.Request(control_url + path, method=method, data=b'' if method == 'POST' else None, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            body = error.read()
            error.close()
            return error.code, json.loads(body or b'null')

    def wait_http(url):
        for _ in range(200):
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(.4)
        raise RuntimeError('Not ready ' + url)

    def acquisition_ready():
        probe = "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8002/health',timeout=1).status==200"
        for _ in range(150):
            if subprocess.run(base + ['exec', '-T', 'acquisition', 'python', '-c', probe], cwd=HERE, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                return
            time.sleep(.2)
        raise RuntimeError('Acquisition not ready')

    def networks(service):
        cid = subprocess.run(base + ['ps', '-q', service], cwd=HERE, env=env, capture_output=True, text=True).stdout.strip()
        raw = subprocess.run(['docker', 'inspect', '--format', '{{json .NetworkSettings.Networks}}', cid], capture_output=True, text=True).stdout
        return sorted(n.removeprefix(project + '_') for n in json.loads(raw or '{}'))

    def running_containers():
        r = subprocess.run(['docker', 'ps', '--format', '{{.ID}} {{.Names}} {{.Image}} {{.Status}}'], capture_output=True, text=True)
        return sorted(line.split(' ', 3)[:3] for line in r.stdout.splitlines() if project not in line)

    def active_base():
        return dict(sha256=sha256_file(ACTIVE_BASE), size=ACTIVE_BASE.stat().st_size, mtime=ACTIVE_BASE.stat().st_mtime) if ACTIVE_BASE.exists() else None

    def state_file(name):
        raw = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'cat', '-v', f'{project}-state:/state:ro',
                              images['backend_client_image'], f'/state/{name}'], capture_output=True).stdout
        return json.loads(raw) if raw else None

    snapshot_rounds = rounds_of(Path(facts['snapshot']))
    isolation_before = dict(active_base=active_base(), containers=running_containers())
    results['isolation_before'] = isolation_before
    for volume in (env['RJ_REAL_PROFILE_VOLUME'], env['RJ_REAL_JOBS_VOLUME'], env['RJ_REAL_STATE_VOLUME']):
        if subprocess.run(['docker', 'volume', 'inspect', volume], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            raise Refused('Volume already exists: ' + volume)
        command(['docker', 'volume', 'create', '--label', 'rj.real.group=' + project, volume], 'volume-' + volume.rsplit('-', 1)[-1])
    checks = {}
    try:
        command(base + ['run', '--rm', '--no-deps', 'profile-admin', 'initialize-profile', '--confirm-new-profile'], 'initialize-profile')
        command(base + ['run', '--rm', '--no-deps', 'backend-client', 'prepare-real'], 'prepare-copy')
        command(base + ['up', '-d', '--no-deps', 'backend-control'], 'start-control')
        wait_http(control_url + '/health')
        status, current = http('/internal/v1/updates/current')
        assert status == 200 and current['scope']['kind'] == 'group', current
        results['before'] = dict(last_published=current['last_published'])
        if a.mode == 'real':
            LEDGER_DIR.mkdir(parents=True, exist_ok=True)
            with open(facts['ledger'], 'x', encoding='utf-8') as f:  # exclusive: a second run can never pass this point
                json.dump(dict(project=project, at=iso(time.time()), images=images, snapshot_sha256=facts['snapshot_sha256']), f)
        command(base + ['up', '-d', '--no-deps', 'acquisition'], 'start-acquisition')
        acquisition_ready()
        checks['acquisition_networks'] = networks('acquisition')
        checks['egress_only_in_real'] = ('egress' in checks['acquisition_networks']) == (a.mode == 'real')
        status, body = http('/internal/v1/updates', 'POST', {'Idempotency-Key': str(uuid.uuid4())})
        assert status == 202, (status, body)
        job = body['job']
        deadline = time.time() + 1500
        while job['status'] in ('queued', 'running') and time.time() < deadline:
            time.sleep(2)
            job = http(f"/internal/v1/updates/{job['id']}")[1]
        results['job'] = job
        save()
        command(base + ['run', '--rm', '--no-deps', 'backend-client', 'real-report'], 'acquisition-report')
        command(base + ['stop', 'acquisition'], 'stop-acquisition')
        _, after = http('/internal/v1/updates/current')
        results['after'] = dict(last_published=after['last_published'], last_job=after['last_job'])
        command(base + ['stop', 'backend-control'], 'stop-control')
        command(base + ['run', '--rm', '--no-deps', 'backend-client', 'real-check'], 'integrity-and-bundle')
        command(base + ['logs', '--no-color'], 'service-logs-secret-audit')
        reports = state_file('acquisition-reports.json') or {}
        check = state_file('real-check-report.json')
        results['real_check'] = check
        sends = {i: [s for s in r.get('sends', []) if s.get('sent')] for i, r in reports.items()}
        # Logical attempts: the adapter's logical requests in the service report, and the transport's attempts that
        # the sealed bundle declares (verified again by real-check). Physical sends: the adapter's sent entries.
        attempts = {i: r.get('requests', []) for i, r in reports.items()}
        bundle_attempts = [s['attempts'] for s in check['sealed']]
        results['counters'] = {i: dict(physical_sends=len(sends[i]), logical_requests=len(attempts[i]), bundle_attempts=bundle_attempts,
                                       limits=reports[i].get('limits'), stopped=reports[i].get('stopped'),
                                       error=reports[i].get('error'), sandbox=reports[i].get('sandbox'),
                                       browser_documents=sorted({d[0] for d in (reports[i].get('browser') or {}).get('documents', [])}),
                                       paths=sorted({s['path'] for s in sends[i]})) for i in reports}
        # Independent of the bundle: every event asked belongs to a declared round of the copy, or is a new match
        # that a declared round's listing showed (the verifier and real-check also check the bundle's calendar).
        round_ids = {i for n in facts['detail_rounds'] for i in snapshot_rounds.get(n, ())}
        new_ids = {m['id'] for s in check['sealed'] for m in s['new_matches']}
        detail_ids = {int(p['path'].split('/')[2]) for r in reports.values() for p in r.get('requests', []) + r.get('sends', [])
                      if (p.get('path') or '').startswith('/event/')}
        checks.update(
            physical_sends_within_limit=all(len(s) <= a.max_sends for s in sends.values()),
            logical_attempts_within_limit=all(len(x) <= a.max_attempts for x in attempts.values())
            and all(n <= a.max_attempts for n in bundle_attempts),
            limits_in_force=all((r.get('limits') or {}).get('sends') == a.max_sends and (r.get('limits') or {}).get('attempts') == a.max_attempts
                                and (r.get('limits') or {}).get('detail_rounds') == facts['detail_rounds'] for r in reports.values()),
            details_only_declared_rounds=detail_ids <= round_ids | new_ids,
            secrets_absent=not any(c['secret_in_output'] for c in results['cases']))
        coverage = (job.get('result') or {}).get('coverage')
        if a.mode == 'rehearsal' and a.scenario == 'ok':
            checks.update(completed=job['status'] == 'completed', published_on_copy=check['generation_after'] != check['generation_before'],
                          restricted_flagged=set(results['expected']['restricted']) <= set(coverage['restricted']),
                          round9_consulted=set(check['sealed'][0]['consulted']) == round_ids)
        elif a.mode == 'rehearsal' and a.scenario == 'budget':
            checks.update(completed=job['status'] == 'completed', published_on_copy=check['generation_after'] != check['generation_before'],
                          budget_pending_reported=bool(coverage['deferred']) and any(r['reason'] == 'budget' for p in coverage['pending'] for r in p['resources']))
        elif a.mode in ('offline',) or a.scenario == 'refusal':
            checks.update(failed_without_publication=job['status'] == 'failed' and not job['published']
                          and check['generation_after'] == check['generation_before'] and not check['sealed']
                          and after['last_published'] == results['before']['last_published'])
            if a.mode == 'offline':
                checks['nothing_answered'] = all(not any(s.get('status') == 200 for s in x) for x in sends.values())
        else:
            checks.update(completed=job['status'] == 'completed')
    finally:
        results['checks'] = checks
        cleanup = subprocess.run(base + ['down', '--remove-orphans'], cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (out / 'cleanup.log').write_bytes(cleanup.stdout + cleanup.stderr)
        for file in files.values():
            file.unlink(missing_ok=True)
        tmp.cleanup()
        isolation_after = dict(active_base=active_base(), containers=running_containers())
        results['isolation'] = dict(active_base_unchanged=isolation_after['active_base'] == isolation_before['active_base'],
                                    other_containers_unchanged=isolation_after['containers'] == isolation_before['containers'],
                                    volumes_kept=[env[k] for k in ('RJ_REAL_PROFILE_VOLUME', 'RJ_REAL_JOBS_VOLUME', 'RJ_REAL_STATE_VOLUME')],
                                    secrets_removed=True)
        results['finished_at'] = iso(time.time())
        results['ok'] = bool(checks) and all(checks.values()) and all(v for k, v in results['isolation'].items() if k != 'volumes_kept')
        save()
    print(json.dumps(dict(ok=results['ok'], mode=a.mode, scenario=a.scenario, project=project, checks=checks,
                          isolation=results['isolation'], counters=results.get('counters')), ensure_ascii=False))
    return 0 if results['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
