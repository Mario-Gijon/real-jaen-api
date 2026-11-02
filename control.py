"""Backend in control mode: the API served from the coordinator's current generation, plus the update control.

    python control.py --base /state/real-jaen.sqlite --ambito ambito.json [--host 0.0.0.0] [--port 8001]

The process owns the coordinator (and so the base lock) for its whole life; every sporting GET captures the current
generation once and a publication swaps it without cutting requests in flight. Same sporting routes and /health as
api.py; api.py itself (static snapshot) is unchanged. Contract: ../rj-analyst/docs/contrato-actualizaciones.md.

Environment: RJ_CONTROL_SECRET_FILE (bearer secret of /internal/v1/updates, shared only with Next's server),
RJ_ACQUISITION_URL and RJ_ACQUISITION_SECRET_FILE (the acquisition service), RJ_UPDATE_RECIPE (the group's by
default; the pilot's for its tests), RJ_ACQUISITION_TEST=1 only on an
installation marked by its test fixture (persistencia.adquisicion, modo 'prueba'). Without the control secret the
mode refuses to start. This module is outside persistencia/: it does not change the coordinator's rule fingerprint.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
import threading
import uuid
from urllib.parse import urlsplit

PILOT = 'sofascore-pilot-16599363-v1'
GROUP = 'sofascore-group2-2627-v1'
# What each recipe really consults, as the interface must say it (never more than the recipe covers).
SCOPES = {
    PILOT: dict(kind='pilot', recipe=PILOT, round=1, match_ids=[16599363], action='Actualizar piloto',
                label='Actualización piloto: calendario de la jornada 1 y un partido',
                description='Consulta el calendario de la jornada 1 y un partido. No actualiza el resto de la jornada '
                            'ni de la temporada: los demás partidos se conservan tal como están publicados.'),
    GROUP: dict(kind='group', recipe=GROUP, round=None, match_ids=[], action='Actualizar Grupo 2',
                label='Actualización del Grupo 2 (Primera Federación 26/27)',
                description='Consulta los partidos finalizados que faltan, los aplazados, los cambios de calendario de '
                            'las jornadas próximas y, por turnos, de las lejanas, y revisa las correcciones de las '
                            'últimas 72 horas. Lo que no se obtenga queda pendiente: se conservan los datos anteriores, '
                            'nunca se sustituyen por ceros.'),
}
RECIPE = GROUP
ACTIVE = ('queued', 'running')
CANCELLABLE_PHASES = ('queued', 'acquiring')
POLL_SECONDS = 3
PREFIX = '/internal/v1/updates'
# Safe public messages per error code; internal reasons stay in the backend log, keyed by job id.
MESSAGES = {
    'ACQUISITION_FAILED': ('La consulta al proveedor no se completó o fue rechazada. No se reintenta sola.', True),
    'ACQUISITION_INTERRUPTED': ('La adquisición se interrumpió antes de terminar.', True),
    'ACQUISITION_UNAVAILABLE': ('El servicio de adquisición no está disponible.', True),
    'CANCELLED': ('Actualización cancelada antes de importar nada.', True),
    'BUNDLE_REJECTED': ('Los datos recibidos no superaron la verificación.', False),
    'GENERATION_CHANGED': ('Los datos publicados cambiaron durante la actualización.', True),
    'PUBLICATION_FAILED': ('No se pudo publicar. Se puede reintentar sin volver a consultar.', True),
    'INTERRUPTED': ('El servidor se reinició durante la actualización.', True),
    'INTERNAL_ERROR': ('Error interno durante la actualización.', False),
}


def now():
    return datetime.now(timezone.utc).isoformat()


def secret(path):
    value = Path(path).read_bytes().strip()
    if not 32 <= len(value) <= 256 or not value.isascii() or any(c <= 32 for c in value):
        raise ValueError('Invalid control secret configuration')
    return value


def restriccion(environ):
    """The restriction a bundle of this installation must declare, if any: RJ_ACQUISITION_MAX_ATTEMPTS,
    RJ_ACQUISITION_MAX_SENDS and RJ_ACQUISITION_DETAIL_ROUNDS, all three or none (the acquisition service has the
    same values; this side refuses a bundle that does not declare exactly them)."""
    names = ('RJ_ACQUISITION_MAX_ATTEMPTS', 'RJ_ACQUISITION_MAX_SENDS', 'RJ_ACQUISITION_DETAIL_ROUNDS')
    values = [environ.get(n) for n in names]
    if not any(values):
        return None
    if not all(values) or not values[0].isdigit() or not values[1].isdigit():
        raise SystemExit('Set the three of ' + ', '.join(names) + ' or none')
    rounds = sorted({int(p) for p in values[2].split(',')})
    return dict(limits=dict(attempts=int(values[0]), sends=int(values[1])), detail_rounds=rounds)


class Unavailable(Exception): pass
class NotCancellable(Exception): pass
class Cancelled(Exception): pass


class Actualizaciones:
    """One active update at a time, durable jobs and idempotency keys under <base>.actualizaciones/control/."""

    def __init__(self, coordinador, cliente, *, recipe=RECIPE):
        if recipe not in SCOPES:
            raise ValueError('Receta no aprobada')
        self.c, self.cliente, self.recipe = coordinador, cliente, recipe
        self.root = coordinador.directorio / 'control'
        self.root.mkdir(exist_ok=True)
        self.guard = threading.RLock()
        self.threads = []
        with closing(self.db()) as c, c:
            c.execute('CREATE TABLE IF NOT EXISTS job(id TEXT PRIMARY KEY, idem TEXT UNIQUE, state TEXT, data TEXT)')
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active ON job((1)) WHERE state IN ('queued','running')")
        self._reconcile()

    def db(self):
        c = sqlite3.connect(self.root / 'jobs.sqlite', timeout=10)
        c.execute('PRAGMA synchronous=FULL')
        return c

    def _rows(self, where='', args=()):
        with closing(self.db()) as c:
            return [json.loads(r[0]) for r in c.execute('SELECT data FROM job ' + where, args).fetchall()]

    def get(self, ident):
        rows = self._rows('WHERE id=?', (ident,))
        if not rows:
            raise KeyError(ident)
        return rows[0]

    def save(self, item):
        with closing(self.db()) as c, c:
            c.execute('UPDATE job SET state=?, data=? WHERE id=?', (item['status'], json.dumps(item), item['id']))

    def _attempt(self, item):
        """Coordinator state of the request's last import attempt (the coordinator's journal is the authority)."""
        path = self.c.directorio / 'adquisiciones' / item['request_id'] / 'solicitud.json'
        if not path.exists():
            return None
        intentos = json.loads(path.read_bytes())['intentos']
        if not intentos:
            return None
        try:
            return self.c.estado(intentos[-1])
        except KeyError:
            return None

    def _reconcile(self):
        """A job left active by a restart is never resumed: completed if its publication was confirmed, else interrupted."""
        for item in self._rows("WHERE state IN ('queued','running')"):
            attempt = self._attempt(item)
            if attempt is not None and attempt['estado'] == 'completed':
                item.update(status='completed', phase='finished', published=True, finished_at=now(),
                            result=dict(published_at=now(), generation=attempt['generacion'],
                                        reused_bundle=item.get('reused_bundle', False)))
            else:
                self._fail(item, 'INTERRUPTED', resumable=False)
            self.save(item)

    def _fail(self, item, code, *, resumable, status='failed'):
        message, retryable = MESSAGES[code]
        item.update(status=status, phase='finished', finished_at=now(), resumable=resumable,
                    error=dict(code=code, message=message, retryable=retryable))

    def _last_finished(self):
        rows = [r for r in self._rows("WHERE state NOT IN ('queued','running')")]
        return max(rows, key=lambda r: r['requested_at']) if rows else None

    def solicitar(self, idem):
        if not isinstance(idem, str) or str(uuid.UUID(idem)) != idem:
            raise ValueError('Idempotency-Key must be a UUID')
        with self.guard:
            known = self._rows('WHERE idem=?', (idem,))
            if known:
                return known[0], True
            active = self._rows("WHERE state IN ('queued','running')")
            if active:
                return active[0], True
            try:
                self.c.capturar()
            except Exception as e:
                raise Unavailable(str(e)) from None
            last = self._last_finished()
            # A failed publication with a verified bundle is retried with the same request: no new provider query.
            reuse = last is not None and last['status'] == 'failed' and last.get('resumable')
            item = dict(id=str(uuid.uuid4()), idem=idem, request_id=last['request_id'] if reuse else str(uuid.uuid4()),
                        status='queued', phase='queued', published=False, requested_at=now(), finished_at=None,
                        acquisition_id=None, cancel_requested=False, reused_bundle=bool(reuse), resumable=False,
                        error=None, result=None)
            with closing(self.db()) as c, c:
                c.execute('INSERT INTO job VALUES(?,?,?,?)', (item['id'], idem, item['status'], json.dumps(item)))
        worker = threading.Thread(target=self.run, args=(item['id'],), daemon=True)
        self.threads.append(worker)
        worker.start()
        return item, False

    def cancelar(self, ident):
        with self.guard:
            item = self.get(ident)
            if item['status'] == 'cancelled':
                return item
            if item['status'] not in ACTIVE or item['phase'] not in CANCELLABLE_PHASES:
                raise NotCancellable()
            item['cancel_requested'] = True
            self.save(item)
            acquisition = item['acquisition_id']
        if acquisition:
            self._cancel_acquisition(acquisition)
        return item

    def _cancel_acquisition(self, acquisition):
        try:
            self.cliente.cancelar(acquisition)
        except Exception as e:  # the acquisition may have just finished; the job still stops before importing
            print(f'[control] cancel {acquisition}: {e}', file=sys.stderr, flush=True)

    def run(self, ident):
        def avance(fase, **datos):
            with self.guard:
                item = self.get(ident)
                cancel = item['cancel_requested']
                first = datos.get('acquisition_id') and item['acquisition_id'] is None
                if first:
                    item['acquisition_id'] = datos['acquisition_id']
                if cancel and fase in ('verifying', 'publishing'):
                    raise Cancelled()
                item.update(status='running', phase=fase)
                self.save(item)
            if cancel and first:
                self._cancel_acquisition(datos['acquisition_id'])
        from persistencia.adquisicion import AdquisicionRechazada
        from persistencia.bundle import Rejected
        try:
            if self.get(ident)['cancel_requested']:
                raise Cancelled()
            result = self.cliente.publicar(self.c, self.get(ident)['request_id'], self.recipe, avance=avance)
            with self.guard:
                item = self.get(ident)
                item.update(status='completed', phase='finished', published=True, finished_at=now(),
                            result=dict(published_at=now(), generation=result['generation_id'],
                                        reused_bundle=item['reused_bundle'], coverage=self._named(result.get('coverage'))))
                self.save(item)
        except Exception as error:
            text = str(error)
            if isinstance(error, Cancelled) or (isinstance(error, AdquisicionRechazada) and 'cancelled' in text):
                code, resumable, status = 'CANCELLED', False, 'cancelled'
            elif isinstance(error, Rejected):
                code, resumable, status = ('GENERATION_CHANGED' if 'generacion' in text.lower() else 'BUNDLE_REJECTED'), False, 'failed'
            elif isinstance(error, AdquisicionRechazada) and 'Publicacion fallida' in text:
                code, resumable, status = 'PUBLICATION_FAILED', True, 'failed'
            elif isinstance(error, AdquisicionRechazada) and 'no se importa' in text:  # the acquisition job ended unsuccessfully
                code = 'ACQUISITION_INTERRUPTED' if 'interrupted' in text else 'ACQUISITION_FAILED'
                resumable, status = False, 'failed'
            elif isinstance(error, AdquisicionRechazada):
                code, resumable, status = 'ACQUISITION_UNAVAILABLE', False, 'failed'
            else:
                code, resumable, status = 'INTERNAL_ERROR', False, 'failed'
            print(f'[control] job {ident}: {code}: {type(error).__name__}: {text[:300]}', file=sys.stderr, flush=True)
            with self.guard:
                item = self.get(ident)
                self._fail(item, code, resumable=resumable, status=status)
                self.save(item)

    def _named(self, coverage):
        """The coverage with the names of the matches it mentions, from the generation just published (the interface
        shows them without asking the data API)."""
        if not coverage:
            return coverage
        ids = {p['event_id'] for p in coverage['pending']} | set(coverage['deferred']) | set(coverage.get('restricted', ()))
        ids |= {m['id'] for m in coverage['new_matches']} | {r for m in coverage['new_matches'] for r in m['replaces']}
        data = self.c.capturar()._datos
        teams = {e['team_id']: e['name'] for e in data['equipos']}
        names = {}
        for m in data['partidos']:
            if m['match_id'] in ids:
                names[str(m['match_id'])] = f"{teams.get(m['home_team_id']) or '?'} – {teams.get(m['away_team_id']) or '?'}"
        return dict(coverage, names=names)

    def public(self, item):
        if item is None:
            return None
        return dict(id=item['id'], status=item['status'], phase=item['phase'],
                    cancellable=item['status'] in ACTIVE and item['phase'] in CANCELLABLE_PHASES and not item['cancel_requested'],
                    cancel_requested=item['cancel_requested'], published=item['published'],
                    requested_at=item['requested_at'], finished_at=item['finished_at'], poll_after_seconds=POLL_SECONDS,
                    error=item['error'],
                    result=None if item['result'] is None else dict(published_at=item['result']['published_at'],
                                                                     reused_bundle=item['result']['reused_bundle'],
                                                                     coverage=item['result'].get('coverage')))

    def actual(self):
        active = self._rows("WHERE state IN ('queued','running')")
        last = self._last_finished()
        published = [r for r in self._rows("WHERE state='completed'")]
        latest = max(published, key=lambda r: r['finished_at']) if published else None
        return dict(scope=SCOPES[self.recipe], active_job=self.public(active[0] if active else None), last_job=self.public(last),
                    # With the coverage of that publication: its pending resources and deferred refreshes stay visible
                    # while their previous data are served, even after a later update failed or was cancelled.
                    last_published=None if latest is None else dict(at=latest['result']['published_at'],
                                                                    coverage=latest['result'].get('coverage')))

    def close(self, timeout=5):
        for thread in self.threads:
            thread.join(timeout)


def dispatch_control(method, path, headers, control, token):
    """Return the legacy control HTTP result, or None when this is not a control request."""
    route = urlsplit(path).path
    if method == 'POST' and not route.startswith(PREFIX):
        return 404, {'error': 'Ruta inexistente'}
    if method == 'GET' and not route.startswith('/internal/'):
        return None
    if method not in ('GET', 'POST'):
        return None

    provided = headers.get('Authorization', '').encode('utf-8')
    if not hmac.compare_digest(provided, b'Bearer ' + token):
        return 401, {'error': 'unauthorized'}
    try:
        if route == PREFIX and method == 'POST':
            item, reused = control.solicitar(headers.get('Idempotency-Key', ''))
            return (200 if reused else 202), {'job': control.public(item), 'reused': reused}
        if route == PREFIX + '/current' and method == 'GET':
            return 200, control.actual()
        match = re.fullmatch(PREFIX + r'/([0-9a-f-]{36})(/cancel)?', route)
        if not match or str(uuid.UUID(match[1])) != match[1]:
            return 404, {'error': 'not_found'}
        if method == 'POST' and match[2]:
            return 202, control.public(control.cancelar(match[1]))
        if method == 'GET' and not match[2]:
            return 200, control.public(control.get(match[1]))
        return 404, {'error': 'not_found'}
    except KeyError:
        return 404, {'error': 'not_found'}
    except (ValueError, TypeError):
        return 400, {'error': 'invalid_request'}
    except NotCancellable:
        return 409, {'error': 'not_cancellable'}
    except Unavailable:
        return 409, {'error': 'update_unavailable'}
    except Exception as e:
        print(f'[control] {type(e).__name__}: {e}', file=sys.stderr, flush=True)
        return 503, {'error': 'unavailable'}


def handler_for(coordinador, control, token):

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, value):
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            result = dispatch_control('POST', self.path, self.headers, control, token)
            return self.reply(*result)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path.startswith('/internal/'):
                result = dispatch_control('GET', self.path, self.headers, control, token)
                return self.reply(*result)
            if url.query:
                return self.reply(400, {'error': 'Esta versión no admite filtros de consulta.'})
            try:
                generacion = coordinador.capturar()  # one generation for the whole request
            except Exception as e:
                print(f'[control] capture: {e}', file=sys.stderr, flush=True)
                return self.reply(503, {'error': 'Datos no disponibles'})
            return self.reply(*generacion.consultar(url.path))
    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', type=Path, required=True)
    ap.add_argument('--ambito', type=Path, default=Path(__file__).with_name('ambito.json'))
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8001)
    args = ap.parse_args()
    if not os.environ.get('RJ_CONTROL_SECRET_FILE'):
        ap.exit(1, 'El modo control necesita RJ_CONTROL_SECRET_FILE\n')
    token = secret(os.environ['RJ_CONTROL_SECRET_FILE'])
    from persistencia.actualizacion import Coordinador
    from persistencia.adquisicion import Cliente
    modo = 'prueba' if os.environ.get('RJ_ACQUISITION_TEST') == '1' else 'real'
    cliente = Cliente(os.environ.get('RJ_ACQUISITION_URL', 'http://acquisition:8002'),
                      os.environ.get('RJ_ACQUISITION_SECRET_FILE', '/run/secrets/acquisition-token'), modo=modo,
                      restriccion=restriccion(os.environ))
    with Coordinador(args.base, args.ambito) as coordinador:
        control = Actualizaciones(coordinador, cliente, recipe=os.environ.get('RJ_UPDATE_RECIPE', RECIPE))
        server = ThreadingHTTPServer((args.host, args.port), handler_for(coordinador, control, token))
        server.daemon_threads = True
        signal.signal(signal.SIGTERM, lambda *a: threading.Thread(target=server.shutdown, daemon=True).start())
        print(f'API (modo control, {modo}): http://{args.host}:{args.port}', flush=True)
        try:
            server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            control.close()


if __name__ == '__main__':
    main()
