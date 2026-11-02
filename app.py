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


def _legacy_response(request: Request):
    query = request.scope.get('query_string', b'')
    if query:
        status, body = 400, {'error': 'Esta versión no admite filtros de consulta.'}
    else:
        # BaseHTTPRequestHandler receives the undecoded request target. ASGI's
        # raw_path preserves percent escapes, which matters for invalid IDs.
        raw_path = request.scope.get('raw_path')
        path = raw_path.decode('latin-1') if raw_path is not None else request.url.path
        status, body = consultar(request.app.state.data, path)

    return LegacyJSONResponse(
        content=body,
        status_code=status,
        headers={'Cache-Control': 'no-store'},
    )


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


def create_app(data=None) -> FastAPI:
    """Build an app, optionally with a preloaded dataset for isolated tests."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        with ExitStack() as resources:
            application.state.data = data if data is not None else _load_data(resources)
            yield

    application = FastAPI(title='Sports Data API', lifespan=lifespan)
    application.include_router(router)
    # The old server accepts trailing slashes without redirecting; the fallback
    # route then lets ``consultar`` decide their exact result.
    application.router.redirect_slashes = False
    return application


app = create_app()
