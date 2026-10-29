"""Backend side of the isolated acquisition test stack (docker/acquisition/compose.test.yaml). Never an API endpoint.

Works on a copy of an identified inactive snapshot inside the test state volume. The acquisition service of the stack
replays, through the SDK and its browser, the original responses of the real bundle of 2026-10-28 from a local site
(no network). Modes are run in order by docker/acquisition/test_integration.py.
"""
import base64
from contextlib import closing
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

from persistencia import lectura, sincronizacion, sqlite
from persistencia.actualizacion import Coordinador
from persistencia.adquisicion import TEST_CONTENT, TEST_MARKER, AdquisicionRechazada, Cliente, verificar_sellado
from persistencia.bloqueo import bloquear
from persistencia.bundle import PILOT, Rejected, encode, inventory, parse, sha

ROOT = Path('/state'); BASE = ROOT / 'base.sqlite'; AMBITO = Path('/app/ambito.json')
SECRET = '/run/secrets/acquisition-token'; URL = 'http://acquisition:8002'; EVIDENCE = Path('/evidence')
RECIPE = 'sofascore-pilot-16599363-v1'


def client(modo='prueba'):
    return Cliente(URL, SECRET, modo=modo)


def rejected(call, kind=(AdquisicionRechazada, Rejected)):
    try:
        call()
    except kind as error:
        return str(error)
    raise AssertionError('Expected rejection')


def save(name, value):
    (ROOT / name).write_bytes(encode(value))
    print(name + ' ' + encode(value).decode()[:1500], flush=True)


def identity():
    with bloquear(BASE), closing(sqlite._lectura(BASE)) as c:
        return {t: [list(r) for r in c.execute(f'SELECT {cols} FROM {t} ORDER BY id').fetchall()] for t, cols in
                [('team', 'id,uid'), ('player', 'id,uid'), ('match', 'id,uid'), ('observation', 'id,content_hash'),
                 ('local_replacement', 'id,replaced_match_id,current_match_id,status')]}


def prepare():
    import importlib.util
    assert importlib.util.find_spec('sofascore_scraper') is None and importlib.util.find_spec('patchright') is None
    assert not Path('/app/acquisition').exists()  # the backend never receives acquisition code
    assert not BASE.exists()
    sqlite.copia_consistente('/imports/snapshot.sqlite', BASE)
    assert sincronizacion.verificar(BASE)['ok']
    # Explicit maintenance on this disposable copy only: the persistence code changed since the snapshot.
    sincronizacion.reconstruir(BASE, confirmar=True, comando='acquisition test copy: explicit rule adoption')
    assert sincronizacion.verificar(BASE)['ok']
    BASE.with_name(BASE.name + TEST_MARKER).write_text(TEST_CONTENT)
    with Coordinador(BASE, AMBITO, reserva=0) as c:
        baseline = inventory(c.capturar())
    save('ids-before.json', identity())
    save('prepare-report.json', dict(revision=sqlite.describir(BASE)['revision'], generation_id=baseline['generation_id'],
                                     inventory_sha256=baseline['sha256'], events=len(baseline['events']),
                                     historical=[e['id'] for e in baseline['events'] if not e['current']]))


def auth_probe():
    for header in (None, 'Bearer deliberately-wrong'):
        request = urllib.request.Request(URL + '/internal/v1/acquisitions', data=b'{}')
        if header:
            request.add_header('Authorization', header)
        try:
            urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            assert error.code == 401 and Path(SECRET).read_bytes().strip() not in error.read()
            error.close()
        else:
            raise AssertionError('Unauthorized request accepted')
    print('MISSING_AND_WRONG_AUTH_REJECTED', flush=True)


def real_bundle():
    """The sealed real bundle of 2026-10-28, verified by the definitive receiver against its own inventory; it can
    never be published into another generation."""
    meta = parse((EVIDENCE / 'bundle-meta.json').read_bytes())
    body = (EVIDENCE / 'bundle.json').read_bytes()
    original = parse((EVIDENCE / 'inventory.json').read_bytes())
    manifest, files = verificar_sellado(body, meta['descriptor'], original, tuple(meta['context']))
    assert manifest['transport'] == 'sofascore-https' and manifest['generation_id'] == original['generation_id']
    as_fixture = rejected(lambda: verificar_sellado(body, meta['descriptor'], original, tuple(meta['context']), test_transport=True))
    with Coordinador(BASE, AMBITO, reserva=0) as c:
        current = inventory(c.capturar())
    stale = rejected(lambda: verificar_sellado(body, meta['descriptor'], current, tuple(meta['context'])))
    save('real-bundle-report.json', dict(accepted_in_own_generation=True, transport=manifest['transport'],
                                         rules_sha256=manifest['rules_sha256'],
                                         attempts=[(a['path'], a['status']) for a in manifest['attempts']],
                                         coverage=len(manifest['coverage']), files=len(files), rejected_as_fixture=as_fixture,
                                         rejected_in_current_generation=stale,
                                         same_events_as_current=current['events'] == original['events']))


def flow():
    c = client()
    # The real client refuses a marked test installation before any request.
    with Coordinador(BASE, AMBITO, reserva=0) as coord:
        g0 = coord.capturar()
        rejected(lambda: client('real').publicar(coord, str(uuid.uuid4()), RECIPE))
        assert coord.capturar() is g0
    before = lectura.cargar_desde_base(BASE, AMBITO)

    # 1. Coordinator failure after the candidate commit: nothing published, old generation kept.
    r1 = str(uuid.uuid4()); visited = []
    def fault(point):
        visited.append(point)
        if point == 'candidate_committed':
            raise RuntimeError('Injected failure after candidate commit')
    with Coordinador(BASE, AMBITO, reserva=0, _frontera=fault) as coord:
        message = rejected(lambda: c.publicar(coord, r1, RECIPE))
        assert 'Publicacion fallida' in message and coord.capturar().id == g0.id and 'candidate_committed' in visited
    folder = BASE.with_name(BASE.name + '.actualizaciones') / 'adquisiciones' / r1
    first = parse((folder / 'solicitud.json').read_bytes())
    assert first['bundle'] is not None and len(first['intentos']) == 1
    job = first['acquisition_id']
    report = c.informe(job)
    sends = len(report['sends'])
    assert report['sandbox'] == {'namespaces': True, 'seccomp': True, 'proc_verified': True}, report['sandbox']

    # 2. Restarted coordinator, same request: same acquisition job and verified bundle, a new import attempt.
    with Coordinador(BASE, AMBITO, reserva=0) as coord:
        result = c.publicar(coord, r1, RECIPE)
        g1 = coord.capturar()
        assert result['state'] == 'published' and result['generation_id'] == g1.id != g0.id
        assert result['acquisition_id'] == job and result['attempts'] == 2
        # 3. Repeating the published request is idempotent.
        assert c.publicar(coord, r1, RECIPE) == result and coord.capturar() is g1
        assert g0.consultar(f'/api/v1/matches/{PILOT}')[0] == 200 and g1.consultar(f'/api/v1/matches/{PILOT}')[0] == 200
        # Backend restriction: CLI maintenance stays rejected while the coordinator owns the base.
        maintained = subprocess.run([sys.executable, '-B', '-m', 'persistencia', 'migrar', '--base', str(BASE)],
                                    capture_output=True, text=True)
        assert maintained.returncode != 0 and 'Base en uso' in maintained.stderr
    assert len(c.informe(job)['sends']) == sends  # no new provider request for the retries
    with Coordinador(BASE, AMBITO, reserva=0) as coord:
        assert c.publicar(coord, r1, RECIPE) == result and coord.capturar().id == g1.id

        # 4. A request prepared on the previous generation: acquired and verified, never published.
        r2 = str(uuid.uuid4()); stale = folder.parent / r2; stale.mkdir()
        (stale / 'inventory.json').write_bytes((folder / 'inventory.json').read_bytes())
        (stale / 'solicitud.json').write_bytes(encode(dict(first, request_id=r2, acquisition_id=None, bundle=None, intentos=[])))
        message = rejected(lambda: c.publicar(coord, r2, RECIPE))
        assert 'generacion' in message and coord.capturar().id == g1.id

        # 5. Tampered bundles are rejected at the trust boundary, before the coordinator.
        body = (folder / 'bundle.json').read_bytes(); baseline = parse((folder / 'inventory.json').read_bytes())
        tampered = {}
        for mutation in ('coverage', 'history', 'inventory', 'payload', 'provenance'):
            altered = parse(body)
            if mutation == 'coverage': altered['manifest']['coverage'][0]['status'] = 'pending'
            elif mutation == 'history': altered['manifest']['retained'] = []
            elif mutation == 'inventory': altered['manifest']['inventory_sha256'] = '0' * 64
            elif mutation == 'payload': altered['files'][next(n for n in altered['files'] if n.startswith('team_'))] = 'e30K'
            else: altered['files']['provenance.jsonl'] = base64.b64encode(b'{}\n').decode()
            raw = encode(altered)
            tampered[mutation] = rejected(lambda: verificar_sellado(raw, dict(bytes=len(raw), sha256=sha(raw)), baseline,
                                                                    (job, r1), test_transport=True), Rejected)
        tampered['descriptor'] = rejected(lambda: verificar_sellado(body + b' ', first['bundle'], baseline, (job, r1),
                                                                    test_transport=True), Rejected)
        assert coord.capturar().id == g1.id
        published = coord.capturar()
    # 6. Identities, originals and local decisions kept; every response equals the independent SQL reader.
    ids_before, ids_after = parse((ROOT / 'ids-before.json').read_bytes()), identity()
    for table in ('team', 'player', 'match', 'local_replacement'):
        assert ids_before[table] == ids_after[table], table
    assert {tuple(r) for r in ids_before['observation']} <= {tuple(r) for r in ids_after['observation']}
    independent = lectura.cargar_desde_base(BASE, AMBITO)
    import api
    from test_carga import rutas
    count = 0
    for route in rutas(independent):
        assert published.consultar(route) == api.consultar(independent, route), route
        count += 1
    changed = [r for r in rutas(before) if api.consultar(before, r) != api.consultar(independent, r)]
    assert sincronizacion.verificar(BASE)['ok']
    save('flow-report.json', dict(publication='completed', generation_before=g0.id, generation_after=g1.id,
                                  first_attempt='failed after candidate commit, old generation kept',
                                  retry='same acquisition job and bundle, new import attempt', attempts=result['attempts'],
                                  provider_sends=sends, sandbox=report['sandbox'], launches=report['launches'],
                                  page_hosts=report['browser']['by_host'], sampled_destinations=report['connections']['by_destination'],
                                  duplicate_publication_idempotent=True, restart_idempotent=True,
                                  stale_generation_rejected=message, tampered_rejected=tampered, routes_equivalent=count,
                                  routes_changed=len(changed), ids_preserved=True, original_observations_preserved=True,
                                  maintenance_rejected_while_owned=True, integrity='ok'))


def restart_start():
    c = client()
    with Coordinador(BASE, AMBITO, reserva=0) as coord:
        baseline = inventory(coord.capturar())
    request = str(uuid.uuid4())
    job = c.solicitar(request, RECIPE, baseline)
    while c.estado(job['id'])['state'] == 'queued':
        time.sleep(.05)
    (ROOT / 'restart.json').write_text(json.dumps(dict(request=request, job=job['id'], generation=baseline['generation_id'])))
    print('RESTART_JOB_RUNNING ' + job['id'], flush=True)


def restart_check():
    c = client(); old = json.loads((ROOT / 'restart.json').read_text())
    assert c.estado(old['job'])['state'] == 'interrupted'
    with Coordinador(BASE, AMBITO, reserva=0) as coord:
        assert coord.capturar().id == old['generation']
        folder = coord.directorio / 'adquisiciones' / old['request']
        folder.mkdir(parents=True)  # the request as the backend would have recorded it before the crash
        baseline = inventory(coord.capturar())
        (folder / 'inventory.json').write_bytes(encode(baseline))
        (folder / 'solicitud.json').write_bytes(encode(dict(request_id=old['request'], recipe_id=RECIPE,
                                                            inventory_sha256=baseline['sha256'], generation_id=baseline['generation_id'],
                                                            acquisition_id=old['job'], bundle=None, intentos=[])))
        message = rejected(lambda: c.publicar(coord, old['request'], RECIPE))
        assert 'interrupted' in message and c.estado(old['job'])['state'] == 'interrupted'
        assert coord.capturar().id == old['generation']
    save('restart-report.json', dict(state='interrupted', resumed=False, published=False, message=message))


def refusal():
    """Provider refusal (403 without challenge): the job fails, nothing is imported, and the persisted block stops the
    next job before any send."""
    c = client(); outcomes = []
    with Coordinador(BASE, AMBITO, reserva=0) as coord:
        generation = coord.capturar().id
        for _ in range(2):
            request = str(uuid.uuid4())
            message = rejected(lambda: c.publicar(coord, request, RECIPE))
            record = parse((coord.directorio / 'adquisiciones' / request / 'solicitud.json').read_bytes())
            report = c.informe(record['acquisition_id'])
            outcomes.append(dict(message=message, sent=len([s for s in report['sends'] if s['sent']]), error=report['error'],
                                 stopped=report['stopped']))
        assert coord.capturar().id == generation
    assert outcomes[0]['sent'] >= 1 and outcomes[1]['sent'] == 0, outcomes
    save('refusal-report.json', dict(outcomes=outcomes, published=False))


def group_check():
    """After the group updates (docker/acquisition/test_group.py): integrity, identities kept, and the group verifier
    rejecting tampered copies of the sealed bundles it published (each against its own inventory and context)."""
    assert sincronizacion.verificar(BASE)['ok']
    ids_before, ids_after = parse((ROOT / 'ids-before.json').read_bytes()), identity()
    for table in ('team', 'player', 'match', 'observation', 'local_replacement'):
        assert {tuple(r) for r in ids_before[table]} <= {tuple(r) for r in ids_after[table]}, table
    folders = sorted((BASE.with_name(BASE.name + '.actualizaciones') / 'adquisiciones').iterdir(), key=lambda f: f.stat().st_mtime)
    sealed = []
    for folder in folders:
        record = parse((folder / 'solicitud.json').read_bytes())
        if record['bundle'] is not None:
            sealed.append((parse((folder / 'bundle.json').read_bytes()), (folder / 'bundle.json').read_bytes(),
                           parse((folder / 'inventory.json').read_bytes()), (record['acquisition_id'], record['request_id'])))
    assert len(sealed) == 3, len(sealed)

    def check(body, baseline, context):
        return verificar_sellado(body, dict(bytes=len(body), sha256=sha(body)), baseline, context, test_transport=True)

    def rebuilt(envelope, name=None, lines=None):
        envelope = json.loads(json.dumps(envelope))
        if name is not None:
            raw = b''.join(encode(line) + b'\n' for line in lines)
            envelope['files'][name] = base64.b64encode(raw).decode()
            entry = next(e for e in envelope['manifest']['files'] if e['name'] == name)
            entry.update(bytes=len(raw), sha256=sha(raw))
        return encode(envelope)

    def lines(envelope, name):
        return [json.loads(line) for line in base64.b64decode(envelope['files'][name]).splitlines()]

    tampered = {}
    first, raw_first, baseline1, context1 = sealed[0]
    second, raw_second, baseline2, context2 = sealed[1]
    for envelope, raw, baseline, context in sealed:
        check(raw, baseline, context)  # each sealed bundle still verifies as it was published
    # A pending resource without data presented as data.
    altered = json.loads(json.dumps(first))
    entry = next(c for c in altered['manifest']['coverage'] if c['status'] == 'pending' and c['origin'] == 'none')
    entry.update(status='data', origin='attempt', reason=None)
    tampered['pending-as-data'] = rejected(lambda: check(rebuilt(altered), baseline1, context1), Rejected)
    # A previous original dropped behind a pending entry.
    altered = json.loads(json.dumps(second))
    entry = next(c for c in altered['manifest']['coverage'] if c['status'] == 'pending' and c['origin'] == 'inventory')
    entry.update(origin='none', evidence=None)
    tampered['previous-dropped'] = rejected(lambda: check(rebuilt(altered), baseline2, context2), Rejected)
    # A new match that no consulted round listed.
    calendar = lines(first, 'calendar.jsonl')
    ghost = dict(json.loads(json.dumps(calendar[0])), id=99999999)
    tampered['unlisted-new-match'] = rejected(lambda: check(rebuilt(first, 'calendar.jsonl', calendar + [ghost]), baseline1, context1), Rejected)
    # An unconsulted original altered.
    kept = next(c for c in first['manifest']['coverage'] if c['status'] == 'data' and c['origin'] == 'inventory' and c['resource'] == 'statistics')
    rows = lines(first, kept['evidence']['file'])
    rows[kept['evidence']['line'] - 1]['payload'] = {'statistics': []}
    tampered['unconsulted-altered'] = rejected(lambda: check(rebuilt(first, kept['evidence']['file'], rows), baseline1, context1), Rejected)
    save('group-check-report.json', dict(integrity='ok', ids_preserved=True, sealed_bundles=len(sealed), tampered_rejected=tampered))


# --- Single real run of the Group 2 recipe and its rehearsals (docker/acquisition/real_group.py) ------------------

def expected_restriction():
    import os
    import control
    return control.restriccion(os.environ)


def prepare_real():
    """Like prepare, for the real run and its rehearsals: the copy must be the identified one (sha256), and only the
    fixture rehearsal marks the installation as a test (the real provider refuses a marked one)."""
    import hashlib
    import os
    expected = os.environ['RJ_REAL_SNAPSHOT_SHA256']
    digest = hashlib.sha256(Path('/imports/snapshot.sqlite').read_bytes()).hexdigest()
    assert digest == expected, ('snapshot', digest)
    assert expected_restriction() is not None, 'the run requires its restriction'
    prepare()
    if os.environ.get('RJ_REAL_PROVIDER') != 'fixture-site':
        BASE.with_name(BASE.name + TEST_MARKER).unlink()
    save('snapshot.json', dict(sha256=digest))


def requests_of_this_copy():
    folder = BASE.with_name(BASE.name + '.actualizaciones') / 'adquisiciones'
    return [parse((f / 'solicitud.json').read_bytes()) | dict(folder=str(f))
            for f in sorted(folder.iterdir(), key=lambda f: f.stat().st_mtime)] if folder.exists() else []


def real_report():
    """The acquisition service's report of each job of this copy (sends, attempts, stops, browser, connections),
    taken while the service is still up and idle; nothing is requested from the provider."""
    import os
    modo = 'prueba' if os.environ.get('RJ_REAL_PROVIDER') == 'fixture-site' else 'real'
    reports = {}
    for record in requests_of_this_copy():
        if record.get('acquisition_id'):
            reports[record['acquisition_id']] = client(modo).informe(record['acquisition_id'])
    save('acquisition-reports.json', reports)


def real_check():
    """With every service stopped: integrity and identities of the copy, and each sealed bundle verified again,
    with exactly the configured restriction, its attempts inside the declared limits and its details only from the
    declared rounds. Summarizes what the run published for the report."""
    import os
    from persistencia.adquisicion import comprobar_restriccion
    from persistencia.bundle import ROUND_PATHS
    test = os.environ.get('RJ_REAL_PROVIDER') == 'fixture-site'
    restriction = expected_restriction()
    assert sincronizacion.verificar(BASE)['ok']
    ids_before, ids_after = parse((ROOT / 'ids-before.json').read_bytes()), identity()
    for table in ('team', 'player', 'match', 'observation', 'local_replacement'):
        assert {tuple(r) for r in ids_before[table]} <= {tuple(r) for r in ids_after[table]}, table
    prepared = parse((ROOT / 'prepare-report.json').read_bytes())
    with Coordinador(BASE, AMBITO, reserva=0) as c:
        generation = c.capturar()
        published = inventory(generation)
        datos = generation._datos
    sealed = []
    for record in requests_of_this_copy():
        if record['bundle'] is None:
            continue
        folder = Path(record['folder'])
        body, baseline = (folder / 'bundle.json').read_bytes(), parse((folder / 'inventory.json').read_bytes())
        manifest, _ = verificar_sellado(body, record['bundle'], baseline, (record['acquisition_id'], record['request_id']),
                                        test_transport=test)
        comprobar_restriccion(manifest, restriction)
        plan, attempts = manifest['plan'], manifest['attempts']
        calendar = {json.loads(l)['id']: json.loads(l) for l in base64.b64decode(
            parse(body)['files']['calendar.jsonl']).splitlines()}
        details = sorted({int(a['path'].split('/')[2]) for a in attempts if a['path'] not in ROUND_PATHS})
        outside = [i for i in details if (calendar[i].get('round') or {}).get('number') not in plan['detail_rounds']]
        assert not outside, ('details outside the declared rounds', outside)
        assert len(attempts) <= plan['limits']['attempts']
        coverage = [c for c in manifest['coverage'] if c['resource'] != 'calendar']
        sealed.append(dict(acquisition_id=record['acquisition_id'], attempts=len(attempts), limits=plan['limits'],
                           detail_rounds=plan['detail_rounds'], listed=sorted(ROUND_PATHS[a['path']] for a in attempts
                                                                              if a['path'] in ROUND_PATHS and a['status'] == 200),
                           failed_rounds=plan['rounds']['failed'], consulted=details,
                           restricted=sorted(int(i) for i, w in plan['deferred'].items() if w == 'restricted'),
                           deferred=sorted(int(i) for i, w in plan['deferred'].items() if w == 'budget'),
                           new=sum(c['status'] == 'data' and c['origin'] == 'attempt' for c in coverage),
                           kept=sum(c['status'] == 'data' and c['origin'] == 'inventory' for c in coverage),
                           pending=sorted((c['event_id'], c['resource'], c['reason']) for c in coverage if c['status'] == 'pending'),
                           new_matches=plan['new_matches']))
    rounds = {}
    for m in datos['partidos']:
        if m['match_id'] in {d for s in sealed for d in s['consulted']}:
            rounds[m['match_id']] = dict(status=m['status'], score=[m.get('home_score'), m.get('away_score')],
                                         sections={k: (datos['originales'].get((m['match_id'], k)) or {}).get('registro', {}).get('fetched_at')
                                                   for k in ('lineups', 'statistics', 'incidents')})
    save('real-check-report.json', dict(integrity='ok', ids_preserved=True, generation_before=prepared['generation_id'],
                                        generation_after=published['generation_id'], sealed=sealed, consulted_matches=rounds))


if __name__ == '__main__':
    {'prepare': prepare, 'auth-probe': auth_probe, 'real-bundle': real_bundle, 'flow': flow, 'group-check': group_check,
     'restart-start': restart_start, 'restart-check': restart_check, 'refusal': refusal,
     'prepare-real': prepare_real, 'real-report': real_report, 'real-check': real_check}[sys.argv[1]]()
