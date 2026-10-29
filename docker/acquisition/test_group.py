"""Group 2 recipe through the single update path, with simulated time, a local site and an isolated copy.

python -X utf8 docker/acquisition/test_group.py --acquisition-image IMG --backend-image IMG \
    --snapshot <copias/...sqlite> --evidence <new folder> [--frontend ../rj-analyst]

The local site serves a season built from the snapshot's own originals: round pages for the 38 rounds and the
events of round 9 turned into results (sections copied from round 8 matches: test data of an isolated copy, never
published elsewhere). Three updates through control.py (the button's path), the acquisition service and the
coordinator:

  1. T1 (the day after round 9): eight results new; one empty statistics answer and one lineups 404 stay
     pending without data (never zero); one match still without result; a far calendar change in the rotation.
  2. T1 + 8 h, small budget: the pending sections go first and are recovered; one correction within 72 h brings a
     new event while its three sections fail (500, 404, empty): the previous sections are kept as evidence, marked as read before
     the corrected score, and their goals and assists leave the analysis; the other refreshes are deferred by the
     budget with their data kept. The far rotation moves on.
  3. T1 + 1 day, small budget: the corrected match's sections are recovered first (pending) and the match without
     result, now finished more than 72 h after kick-off, is recovered too; the remaining refreshes are deferred.
  4. T1 + 25 h: a calendar change asks for one event; the browser gets a 500 and the retry a 403 without challenge
     (a refusal, not the normal challenge): the job fails and nothing is published. The deferred refreshes of the
     last publication stay visible in the status while their previous data are served.

With --frontend (a production build of rj-analyst), a real Next server over the isolated copy shows the corrected
match with the review notice after the second update and without it after the third (screenshots at 1440 and 390 px).

Then, with the backend stopped: integrity, identities and four tampered group bundles rejected.
"""
import argparse
import base64
import copy
from datetime import datetime, timezone
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
SECTIONS = ('lineups', 'statistics', 'incidents')


def iso(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--acquisition-image', required=True)
    ap.add_argument('--backend-image', required=True)
    ap.add_argument('--snapshot', type=Path, required=True)
    ap.add_argument('--evidence', type=Path, required=True)
    ap.add_argument('--control-port', type=int, default=18002)
    ap.add_argument('--frontend', type=Path, help='rj-analyst with a production build: checks the match page')
    ap.add_argument('--frontend-port', type=int, default=3311)
    a = ap.parse_args()
    if a.snapshot.name == 'real-jaen.sqlite' or a.snapshot.parent.name != 'copias':
        raise SystemExit('Only an identified inactive copy from copias/, never the active base')
    out = a.evidence.resolve()
    out.mkdir(parents=True, exist_ok=False)
    project = 'rj-grp-test-' + uuid.uuid4().hex[:10]
    tokens = {name: secrets.token_hex(32) for name in ('acquisition', 'control')}
    files = {}
    for name, value in tokens.items():
        files[name] = out / f'{name}-secret'
        files[name].write_text(value, encoding='ascii')
    tmp = tempfile.TemporaryDirectory(prefix=project + '-')
    sys.path.insert(0, str(ROOT))
    from persistencia.sqlite import copia_consistente
    from persistencia.actualizacion import preparar
    from persistencia.bundle import inventory
    from acquisition.group_plan import rounds_to_list
    snapshot = Path(tmp.name) / 'inactive-snapshot.sqlite'
    copia_consistente(a.snapshot, snapshot)
    generation = preparar(snapshot, ROOT / 'ambito.json', 'world')
    baseline = inventory(generation)
    originals = {(i, k): v['registro'] for (i, k), v in generation._datos['originales'].items()}
    by_round = {}
    for e in baseline['events']:
        payload = (originals.get((e['id'], 'event')) or {}).get('payload')
        if e['current'] and payload:
            by_round.setdefault(payload['roundInfo']['round'], []).append(payload)
    nine = sorted(by_round[9], key=lambda p: p['startTimestamp'])
    late, corrected, empty_stats, missing_lineups = nine[0], nine[1], nine[5], nine[6]
    donors = sorted(by_round[8], key=lambda p: p['id'])
    t1 = max(p['startTimestamp'] for p in nine) + 18 * 3600
    times = dict(t1=t1, t2=t1 + 8 * 3600, t3=t1 + 24 * 3600, t4=t1 + 25 * 3600)
    assert times['t3'] - late['startTimestamp'] > 72 * 3600, 'the late result must fall outside the 72 h window'
    assert times['t2'] - corrected['startTimestamp'] < 72 * 3600 + 8 * 3600
    far_round = rounds_to_list(baseline, t1)[1][0]
    moved = sorted(by_round[far_round], key=lambda p: p['id'])[0]
    # Stage 4: a calendar change in a near round after the 9th, whose event request is refused.
    refused_round = min(r for r in rounds_to_list(baseline, times['t4'])[0] if r > 9)
    refused = sorted(by_round[refused_round], key=lambda p: p['id'])[0]
    results = dict(project=project, cases=[], times={k: iso(v) for k, v in times.items()}, far_round=far_round,
                   refused_round=refused_round,
                   matches=dict(late=late['id'], corrected=corrected['id'], empty_stats=empty_stats['id'],
                                missing_lineups=missing_lineups['id'], moved=moved['id'], refused=refused['id']))

    def finished(p, score):
        e = copy.deepcopy(p)
        e['status'] = dict(code=100, description='Ended', type='finished')
        e['homeScore'], e['awayScore'] = dict(current=score[0], display=score[0]), dict(current=score[1], display=score[1])
        return e

    def world(stage):
        """{path: bytes} served by the local site at one stage."""
        events = {}
        for number, payloads in by_round.items():
            for p in payloads:
                events[p['id']] = copy.deepcopy(p)
        for k, p in enumerate(nine):
            if p['id'] == late['id'] and stage < 3:
                continue  # no result yet
            events[p['id']] = finished(p, (k % 3, 1))
        if stage >= 2:
            events[corrected['id']] = finished(corrected, (4, 4))  # a correction of the score within 72 h
        moved_event = copy.deepcopy(events[moved['id']])
        moved_event['startTimestamp'] += 86400  # a far calendar change
        events[moved['id']] = moved_event
        if stage == 4:
            refused_event = copy.deepcopy(events[refused['id']])
            refused_event['startTimestamp'] += 2 * 3600
            events[refused['id']] = refused_event
        routes = {}
        for number in by_round:
            page = [events[p['id']] for p in by_round[number]]
            routes[f'/unique-tournament/17073/season/97382/events/round/{number}'] = json.dumps({'events': page}).encode()
        for ident, e in events.items():
            routes[f'/event/{ident}'] = json.dumps({'event': e}).encode()
        for k, p in enumerate(nine):
            donor = donors[k % len(donors)]['id']
            for key in SECTIONS:
                routes[f'/event/{p["id"]}/{key}'] = json.dumps(originals[(donor, key)]['payload']).encode()
        if stage == 1:
            routes[f'/event/{empty_stats["id"]}/statistics'] = b'{"statistics":[]}'
            routes.pop(f'/event/{missing_lineups["id"]}/lineups')  # 404
        if stage == 2:
            # Three failures that keep the previous sections: a 500 (retried once), a 404 and an empty answer. Not
            # three 500 in a row: the SDK's breaker opens at 3 consecutive server errors and stops everything.
            routes[f'/event/{corrected["id"]}/statistics'] = b'__500__'
            routes.pop(f'/event/{corrected["id"]}/lineups')
            routes[f'/event/{corrected["id"]}/incidents'] = b'{"incidents":[]}'
        if stage == 4:
            routes[f'/event/{refused["id"]}'] = b'__500_403__'  # 500 to the browser, then 403 without challenge
        return routes

    env = os.environ.copy()
    env.update(RJ_TEST_ACQUISITION_IMAGE=a.acquisition_image, RJ_TEST_BACKEND_IMAGE=a.backend_image, RJ_TEST_MODE='1',
               RJ_TEST_SECRET_FILE=str(files['acquisition']), RJ_TEST_CONTROL_SECRET_FILE=str(files['control']),
               RJ_TEST_SNAPSHOT=str(snapshot), RJ_TEST_CONTROL_PORT=str(a.control_port),
               RJ_TEST_RECIPE='sofascore-group2-2627-v1', RJ_TEST_ROUTES=str(Path(tmp.name) / 'routes-1.json'),
               RJ_TEST_PROFILE_VOLUME=project + '-profiles', RJ_TEST_JOBS_VOLUME=project + '-jobs',
               RJ_TEST_STATE_VOLUME=project + '-state', RJ_TEST_EVIDENCE_VOLUME='rj-real-sdk-37b257e89b03-exchange')
    base = ['docker', 'compose', '-p', project, '-f', str(ROOT / 'docker/acquisition/compose.test.yaml'), '--profile', 'updates']
    control_url = f'http://127.0.0.1:{a.control_port}'

    def save():
        (out / 'results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    def command(cmd, expect=0, name=None, timeout=900):
        r = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        combined = r.stdout + r.stderr
        assert not any(v.encode() in combined for v in tokens.values()), ('secret in output', name)
        if name:
            (out / (name + '.log')).write_bytes(combined)
            results['cases'].append(dict(case=name, exit=r.returncode, ok=r.returncode == expect))
            save()
        assert r.returncode == expect, (name, combined.decode(errors='replace')[-3000:])
        return r.stdout

    def http(path, method='GET', headers=None, token=True):
        h = dict(headers or {})
        if token:
            h['Authorization'] = 'Bearer ' + tokens['control']
        req = urllib.request.Request(control_url + path, method=method, data=b'' if method == 'POST' else None, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            body = error.read()
            error.close()
            return error.code, json.loads(body or b'null')

    def wait_health(url):
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
            if subprocess.run(base + ['exec', '-T', 'acquisition', 'python', '-c', probe], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                return
            time.sleep(.2)
        raise RuntimeError('Acquisition not ready')

    def press(stage, budget=None, expect='completed'):
        routes = Path(tmp.name) / f'routes-{stage}.json'
        routes.write_text(json.dumps({p: base64.b64encode(b).decode() for p, b in world(stage).items()}), encoding='utf-8')
        env.update(RJ_TEST_ROUTES=str(routes), RJ_TEST_NOW=iso(times[f't{stage}']), RJ_TEST_PLANNED_ATTEMPTS=str(budget or ''))
        command(base + ['up', '-d', '--no-deps', '--force-recreate', 'acquisition'], name=f'acquisition-stage-{stage}')
        acquisition_ready()
        status, body = http('/internal/v1/updates', 'POST', {'Idempotency-Key': str(uuid.uuid4())})
        assert status == 202, (status, body)
        job = body['job']
        for _ in range(600):
            status, job = http(f"/internal/v1/updates/{job['id']}")
            if job['status'] not in ('queued', 'running'):
                break
            time.sleep(1)
        results[f'press-{stage}'] = job
        save()
        assert job['status'] == expect, job
        return job['result']['coverage'] if expect == 'completed' else job

    def match(ident, part=''):
        return http(f'/api/v1/matches/{ident}{part}', token=False)[1]

    front_url = f'http://127.0.0.1:{a.frontend_port}'
    next_process = None

    def dashboard(phase, ident):
        """The corrected match's page in the real dashboard over this isolated copy (only with --frontend)."""
        if a.frontend is None:
            return
        (out / 'capturas').mkdir(exist_ok=True)
        r = subprocess.run(['node', 'scripts/validate-match-review.mjs', phase, front_url, str(ident), str(out / 'capturas')],
                           cwd=a.frontend, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        report = json.loads(r.stdout.decode().strip().splitlines()[-1])
        results['dashboard-' + phase] = report
        save()
        assert report['ok'], report

    routes_initial = Path(tmp.name) / 'routes-1.json'
    routes_initial.write_text('{}', encoding='utf-8')
    for volume in (env['RJ_TEST_PROFILE_VOLUME'], env['RJ_TEST_JOBS_VOLUME'], env['RJ_TEST_STATE_VOLUME']):
        assert subprocess.run(['docker', 'volume', 'inspect', volume], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
        command(['docker', 'volume', 'create', '--label', 'rj.group.test=' + project, volume])
    try:
        command(base + ['run', '--rm', '--no-deps', 'profile-admin', 'initialize-profile', '--confirm-new-profile'], name='initialize-profile')
        command(base + ['run', '--rm', '--no-deps', 'backend-client', 'prepare'], name='prepare-isolated-copy')
        command(base + ['up', '-d', '--no-deps', 'backend-control'], name='start-control')
        wait_health(control_url + '/health')
        if a.frontend is not None:
            node_env = dict(env, DATA_MODE='real', LOCAL_DATA_API_URL=control_url, NODE_ENV='production')
            log = (out / 'next-server.log').open('wb')
            next_process = subprocess.Popen(['node', 'node_modules/next/dist/bin/next', 'start', '-H', '127.0.0.1', '-p',
                                             str(a.frontend_port)], cwd=a.frontend, env=node_env, stdout=log, stderr=subprocess.STDOUT)
            wait_health(front_url + '/api/v1/source')
        status, current = http('/internal/v1/updates/current')
        assert current['scope']['kind'] == 'group', current
        ids = results['matches']
        before = dict(moved=match(ids['moved'])['data']['kickoff_utc'])

        # 1. T1: eight new results, two sections pending without data, a match without result, a far change.
        c1 = press(1)
        pending1 = {(p['event_id'], r['resource']): r for p in c1['pending'] for r in p['resources']}
        assert pending1.get((ids['empty_stats'], 'statistics'), {}).get('reason') == 'empty', c1
        assert pending1.get((ids['missing_lineups'], 'lineups'), {}).get('reason') == 'not_found', c1
        assert all(not r['kept'] for r in pending1.values()), c1
        assert match(ids['empty_stats'], '/statistics')['data'] is None  # pending: no data, never zeros
        assert match(ids['missing_lineups'], '/lineups')['data'] is None
        assert match(ids['late'])['data']['status'] == 'notstarted'
        assert far_round in c1['rounds']['far']
        after_moved = match(ids['moved'])['data']['kickoff_utc']
        assert after_moved != before['moved'], (before, after_moved)  # the far calendar change is published
        assert c1['new'] > 0 and c1['kept'] > 0
        results['check-1'] = dict(pending=sorted(f'{k[0]}/{k[1]}:{v["reason"]}' for k, v in pending1.items()),
                                  new=c1['new'], kept=c1['kept'], far=c1['rounds']['far'], moved=[before['moved'], after_moved])

        # 2. T1 + 8 h, budget for the pending sections and one refresh: pending first, a correction with kept sections.
        sections_before = {key: match(ids['corrected'], '/' + key) for key in SECTIONS}
        c2 = press(2, budget=len(c1['rounds']['near']) + 6 + 4 + 4)
        assert match(ids['empty_stats'], '/statistics')['data'] is not None, 'pending statistics recovered first'
        assert match(ids['missing_lineups'], '/lineups')['data'] is not None, 'pending lineups recovered first'
        detail = match(ids['corrected'])['data']
        assert (detail['home_score'], detail['away_score']) == (4, 4), detail  # new event version
        # The previous sections are served with the new event as evidence: same data and provenance, no discrepancy
        # (only the count of identical copies grows, as every bundle carries the kept row again), and marked as read
        # before the corrected score.
        same = lambda s: (s['data'], s['meta']['fetched_at'], s['meta']['availability'], s['meta']['discrepancy'])  # noqa: E731
        for key in SECTIONS:
            after = match(ids['corrected'], '/' + key)
            assert same(after) == same(sections_before[key]) and after['data'], ('previous section kept', key)
            assert sections_before[key]['meta']['predates_score_correction'] is False, key
            assert after['meta']['predates_score_correction'] is True, ('marked as not updated', key)
        # Their goals and assists leave the analysis: the old line-ups do not describe the corrected score.
        played = [p for p in match(ids['corrected'], '/lineups')['normalized']['players'] if p['participation'] == 'played']
        assert played and all(p['goals']['value'] is None and p['assists']['value'] is None
                              and 'lineups_before_score_correction' in p['issues'] for p in played), played[:2]
        pending2 = {(p['event_id'], r['resource']): r for p in c2['pending'] for r in p['resources']}
        reasons = dict(statistics='error', lineups='not_found', incidents='empty')
        assert pending2 == {(ids['corrected'], key): dict(resource=key, reason=reasons[key], kept=True) for key in SECTIONS}, c2
        dashboard('pending', ids['corrected'])
        assert c2['deferred'], 'the other refreshes are deferred by the budget'
        assert not set(c2['rounds']['far']) & set(c1['rounds']['far']), 'the far rotation moves on to other rounds'
        results['check-2'] = dict(pending=c2['pending'], deferred=c2['deferred'], far=c2['rounds']['far'], new=c2['new'], kept=c2['kept'])

        # 3. T1 + 1 day, small budget: the corrected match's pending sections first, then the late match finished more
        # than 72 h after kick-off (its reason is "finished", not the 72 h rule); the other refreshes are deferred.
        c3 = press(3, budget=len(c2['rounds']['near']) + 6 + 4 + 4 + 2)
        for key in SECTIONS:
            assert match(ids['corrected'], '/' + key)['meta']['predates_score_correction'] is False, ('re-read', key)
        assert any(p['goals']['value'] is not None for p in match(ids['corrected'], '/lineups')['normalized']['players']
                   if p['participation'] == 'played'), 'goals are used again once the line-ups are re-read'
        dashboard('recovered', ids['corrected'])
        late_now = match(ids['late'])['data']
        assert late_now['status'] == 'finished', late_now
        for key in SECTIONS:
            assert match(ids['late'], '/' + key)['data'] is not None, key
        assert not c3['pending'], c3
        assert c3['deferred'], 'refreshes deferred by the budget'
        assert not set(c3['rounds']['far']) & set(c2['rounds']['far']), 'and on again'
        results['check-3'] = dict(pending=c3['pending'], new=c3['new'], kept=c3['kept'], deferred=c3['deferred'], far=c3['rounds']['far'])
        save()

        # 4. T1 + 25 h: 500 in the browser, then 403 without challenge on the retry: a refusal, nothing published.
        _, before4 = http('/internal/v1/updates/current')
        deferred_before = {m: match(m) for m in c3['deferred']}
        kickoff_before = match(ids['refused'])['data']['kickoff_utc']
        job4 = press(4, expect='failed')
        assert job4['published'] is False and job4['result'] is None, job4
        _, after4 = http('/internal/v1/updates/current')
        assert after4['last_job']['id'] == job4['id'] and after4['last_job']['status'] == 'failed', after4
        assert after4['last_published'] == before4['last_published'], 'the last publication is unchanged'
        assert match(ids['refused'])['data']['kickoff_utc'] == kickoff_before, 'the listing change was not published'
        # The deferred refreshes of the last publication stay visible, named, while their previous data are served.
        last = after4['last_published']['coverage']
        assert last['deferred'] == c3['deferred'] and all(str(m) in last['names'] for m in c3['deferred']), last
        assert all(match(m) == body for m, body in deferred_before.items()), 'deferred matches keep their data'
        results['check-4'] = dict(error=job4['error'], last_published=after4['last_published']['at'],
                                  deferred_visible=last['deferred'])
        save()

        command(base + ['stop', 'backend-control'], name='stop-control')
        command(base + ['run', '--rm', '--no-deps', 'backend-client', 'group-check'], name='integrity-and-tampering')
        command(base + ['logs', '--no-color'], name='service-logs-secret-audit')
        results['ok'] = True
        save()
        print('PASS ' + str(out), flush=True)
    finally:
        if next_process is not None:
            next_process.terminate()
            next_process.wait(30)
        cleanup = subprocess.run(base + ['down', '--remove-orphans'], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (out / 'cleanup.log').write_bytes(cleanup.stdout + cleanup.stderr)
        for file in files.values():
            file.unlink(missing_ok=True)
        tmp.cleanup()
        results['secrets_removed'] = True
        save()


if __name__ == '__main__':
    main()
