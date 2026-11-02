"""FastAPI ASGI entry point for the sports-data read API.

The legacy HTTP server remains available through ``api.py``. Both transports use
the same startup loader and ``queries.consultar`` response contract.

Run with ``uvicorn app:app``. By default this reads ``datos/`` as the legacy
server does. Set ``SPORTS_DATA_STORAGE=sql`` and ``SPORTS_DATA_DB`` to read one
SQLite generation; the cooperative lock is kept until the ASGI app shuts down.
"""
from contextlib import ExitStack, asynccontextmanager
import os
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

import api as legacy_api
from queries import consultar


ROOT = Path(__file__).resolve().parent


class LegacyJSONResponse(JSONResponse):
    """Keep the content type used by ``api.py`` for existing GET routes."""

    media_type = 'application/json; charset=utf-8'


def _load_data(resources):
    storage = os.environ.get('SPORTS_DATA_STORAGE', 'json')
    scope = Path(os.environ.get('SPORTS_DATA_SCOPE', str(ROOT / 'ambito.json')))

    if storage == 'json':
        folder = Path(os.environ.get('SPORTS_DATA_DIR', str(ROOT / 'datos')))
        replacements = os.environ.get('SPORTS_DATA_REPLACEMENTS')
        return legacy_api.cargar(folder, scope, Path(replacements) if replacements else None)

    if storage == 'sql':
        database = os.environ.get('SPORTS_DATA_DB')
        if not database:
            raise ValueError('SPORTS_DATA_DB es obligatorio con SPORTS_DATA_STORAGE=sql')

        from persistencia.bloqueo import bloquear
        from persistencia.lectura import BaseNoUtilizable, cargar_desde_base

        database_path = Path(database)
        if not database_path.is_file():
            raise BaseNoUtilizable(f'No existe la base {database_path} (el modo SQL nunca crea una)')
        resources.enter_context(bloquear(database_path))
        return cargar_desde_base(database_path, scope)

    raise ValueError(f'SPORTS_DATA_STORAGE debe ser json o sql, no {storage!r}')


def _control_enabled():
    value = os.environ.get('SPORTS_DATA_CONTROL', '0')
    if value not in ('0', '1'):
        raise ValueError('SPORTS_DATA_CONTROL debe ser 0 o 1')
    return value == '1'


def _load_control(resources):
    """Start the existing update coordinator only for an explicitly configured SQL installation."""
    from multiprocessing import parent_process

    if parent_process() is not None:
        raise ValueError('El modo control de sports-data no admite procesos worker')
    workers = (os.environ.get('SPORTS_DATA_WORKERS'), os.environ.get('WEB_CONCURRENCY'))
    if any(value is not None and value != '1' for value in workers):
        raise ValueError('El modo control de sports-data solo admite un worker')
    if os.environ.get('SPORTS_DATA_STORAGE', 'json') != 'sql':
        raise ValueError('El modo control requiere SPORTS_DATA_STORAGE=sql')
    database = os.environ.get('SPORTS_DATA_DB')
    if not database:
        raise ValueError('SPORTS_DATA_DB es obligatorio en modo control')
    scope = Path(os.environ.get('SPORTS_DATA_SCOPE', str(ROOT / 'ambito.json')))

    from control import Actualizaciones, RECIPE, restriccion, secret
    from persistencia.actualizacion import Coordinador
    from persistencia.adquisicion import Cliente

    control_secret_file = os.environ.get('RJ_CONTROL_SECRET_FILE')
    if not control_secret_file:
        raise ValueError('El modo control necesita RJ_CONTROL_SECRET_FILE')
    token = secret(control_secret_file)
    mode = 'prueba' if os.environ.get('RJ_ACQUISITION_TEST') == '1' else 'real'
    client = Cliente(
        os.environ.get('RJ_ACQUISITION_URL', 'http://acquisition:8002'),
        os.environ.get('RJ_ACQUISITION_SECRET_FILE', '/run/secrets/acquisition-token'),
        modo=mode,
        restriccion=restriccion(os.environ),
    )
    coordinator = Coordinador(database, scope)
    resources.enter_context(coordinator)
    updates = Actualizaciones(coordinator, client, recipe=os.environ.get('RJ_UPDATE_RECIPE', RECIPE))
    # ExitStack closes the update worker before releasing the coordinator's base lock.
    resources.callback(updates.close)
    return coordinator, updates, token


def _legacy_response(request: Request):
    query = request.scope.get('query_string', b'')
    # BaseHTTPRequestHandler receives the undecoded request target. ASGI's
    # raw_path preserves percent escapes, which matters for invalid IDs.
    raw_path = request.scope.get('raw_path')
    path = raw_path.decode('latin-1') if raw_path is not None else request.url.path
    if query:
        status, body = 400, {'error': 'Esta versión no admite filtros de consulta.'}
    elif getattr(request.app.state, 'control_mode', False):
        try:
            generation = request.app.state.coordinator.capturar()
        except Exception as e:
            import sys
            print(f'[control] capture: {e}', file=sys.stderr, flush=True)
            status, body = 503, {'error': 'Datos no disponibles'}
        else:
            # Capture once for the full request. A later publication only affects the next request.
            status, body = generation.consultar(path)
    else:
        status, body = consultar(request.app.state.data, path)

    return LegacyJSONResponse(
        content=body,
        status_code=status,
        headers={'Cache-Control': 'no-store'},
    )


updates_router = APIRouter()


def _updates_response(request: Request):
    from control import dispatch_control

    path = request.scope.get('raw_path')
    path = path.decode('latin-1') if path is not None else request.url.path
    result = dispatch_control(
        request.method,
        path,
        request.headers,
        request.app.state.updates,
        request.app.state.control_token,
    )
    if result is None:
        result = 404, {'error': 'Ruta inexistente'}
    status, body = result
    return LegacyJSONResponse(content=body, status_code=status, headers={'Cache-Control': 'no-store'})


@updates_router.api_route('/internal/{path:path}', methods=['GET', 'POST'], include_in_schema=False)
def internal_updates(request: Request):
    return _updates_response(request)


@updates_router.post('/', include_in_schema=False)
@updates_router.post('/{path:path}', include_in_schema=False)
def unsupported_control_post(request: Request):
    return _updates_response(request)


router = APIRouter()


@router.get('/', include_in_schema=False)
def root(request: Request):
    return _legacy_response(request)


@router.get('/health')
def health(request: Request):
    return _legacy_response(request)


@router.get('/api/v1/scope')
def scope(request: Request):
    return _legacy_response(request)


@router.get('/api/v1/teams/{ident}')
def team(request: Request, ident: str):
    return _legacy_response(request)


@router.get('/api/v1/teams/{ident}/matches')
def team_matches(request: Request, ident: str):
    return _legacy_response(request)


@router.get('/api/v1/teams/{ident}/players')
def team_players(request: Request, ident: str):
    return _legacy_response(request)


@router.get('/api/v1/teams/{ident}/performance')
def team_performance(request: Request, ident: str):
    return _legacy_response(request)


@router.get('/api/v1/teams/{ident}/player-stats')
def team_player_stats(request: Request, ident: str):
    return _legacy_response(request)


@router.get('/api/v1/players/{ident}')
def player(request: Request, ident: str):
    return _legacy_response(request)


@router.get('/api/v1/players/{ident}/matches')
def player_matches(request: Request, ident: str):
    return _legacy_response(request)


@router.get('/api/v1/matches/{ident}')
def match(request: Request, ident: str):
    return _legacy_response(request)


@router.get('/api/v1/matches/{ident}/{section}')
def match_section(request: Request, ident: str, section: str):
    return _legacy_response(request)


@router.get('/{path:path}', include_in_schema=False)
def fallback(request: Request, path: str):
    # Unknown paths and trailing slashes are passed through so their legacy
    # status and JSON error bodies remain unchanged.
    return _legacy_response(request)


def create_app(data=None, *, control_mode=None) -> FastAPI:
    """Build an app, optionally with a preloaded dataset for isolated tests."""
    control_mode = _control_enabled() if control_mode is None else control_mode
    if control_mode and data is not None:
        raise ValueError('El modo control necesita la generación de SQLite, no datos precargados')

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        with ExitStack() as resources:
            application.state.control_mode = control_mode
            if control_mode:
                (application.state.coordinator,
                 application.state.updates,
                 application.state.control_token) = _load_control(resources)
            else:
                application.state.data = data if data is not None else _load_data(resources)
            yield

    application = FastAPI(title='Sports Data API', lifespan=lifespan)
    if control_mode:
        application.include_router(updates_router)
    application.include_router(router)
    # The old server accepts trailing slashes without redirecting; the fallback
    # route then lets ``consultar`` decide their exact result.
    application.router.redirect_slashes = False
    return application


app = create_app()
