"""HTTP parity checks between the legacy server and the parallel FastAPI app.

Requires ``requirements-fastapi.txt``. Both servers use the same synthetic
JSON fixture and local replacement decision; no external services are used.
"""
import importlib.util
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request
from pathlib import Path


if not all(importlib.util.find_spec(name) for name in ('fastapi', 'uvicorn')):
    raise unittest.SkipTest('instala requirements-fastapi.txt para las pruebas de equivalencia FastAPI')


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'datos'
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import api  # noqa: E402
import app as fastapi_entry  # noqa: E402
from test_carga import AMBITO, Base as BaseCarga  # noqa: E402


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _get(base, route):
    try:
        response = urllib.request.urlopen(base + route, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        headers = {name.lower(): response.headers[name] for name in ('Content-Type', 'Cache-Control')}
        return response.status, headers, json.loads(response.read().decode('utf-8'))


class FastAPIEquivalence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='sports-data-fastapi-')
        cls.replacements = Path(cls.temp.name) / 'replacements.json'
        cls.replacements.write_text(json.dumps({'sustituciones': [{
            'sustituido': 1012,
            'vigente': 1006,
            'motivo': 'Aplazado y jugado en otra fecha con otro id',
            'procedencia': {
                'decidido_por': 'Analista (prueba)',
                'fecha': '2026-10-22',
                'evidencia': 'Mismo cruce y jornada',
            },
        }]}), encoding='utf-8')

        cls.legacy_port = _free_port()
        cls.fastapi_port = _free_port()
        while cls.fastapi_port == cls.legacy_port:
            cls.fastapi_port = _free_port()

        env = dict(os.environ)
        env.update({
            'SPORTS_DATA_STORAGE': 'json',
            'SPORTS_DATA_DIR': str(FIXTURE),
            'SPORTS_DATA_REPLACEMENTS': str(cls.replacements),
            'PYTHONUTF8': '1',
        })
        cls.legacy = subprocess.Popen(
            [sys.executable, '-B', str(ROOT / 'api.py'), '--datos', str(FIXTURE),
             '--sustituciones', str(cls.replacements), '--port', str(cls.legacy_port)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        cls.fastapi = subprocess.Popen(
            [sys.executable, '-B', '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1',
             '--port', str(cls.fastapi_port), '--log-level', 'warning'],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        cls.legacy_url = f'http://127.0.0.1:{cls.legacy_port}'
        cls.fastapi_url = f'http://127.0.0.1:{cls.fastapi_port}'

        deadline = time.monotonic() + 15
        ready = set()
        while time.monotonic() < deadline and len(ready) < 2:
            for name, process, base in (
                ('legacy', cls.legacy, cls.legacy_url),
                ('fastapi', cls.fastapi, cls.fastapi_url),
            ):
                if name in ready:
                    continue
                if process.poll() is not None:
                    cls.tearDownClass()
                    raise RuntimeError(f'{name} terminó durante el arranque (código {process.returncode})')
                try:
                    _get(base, '/health')
                    ready.add(name)
                except OSError:
                    time.sleep(0.05)
        if len(ready) != 2:
            cls.tearDownClass()
            raise RuntimeError('No arrancaron ambos servidores de prueba')

    @classmethod
    def tearDownClass(cls):
        for name in ('fastapi', 'legacy'):
            process = getattr(cls, name, None)
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        temporary = getattr(cls, 'temp', None)
        if temporary is not None:
            temporary.cleanup()

    @classmethod
    def routes(cls):
        data = api.cargar(FIXTURE, sustituciones=cls.replacements)
        routes = ['/health', '/api/v1/scope']
        for team in data['equipos']:
            ident = team['team_id']
            routes.extend((f'/api/v1/teams/{ident}',
                           f'/api/v1/teams/{ident}/matches',
                           f'/api/v1/teams/{ident}/players',
                           f'/api/v1/teams/{ident}/performance',
                           f'/api/v1/teams/{ident}/player-stats'))
        player_ids = {player['player_id'] for player in data['jugadores']} | set(data['jugadores_excluidos'])
        for ident in sorted(player_ids):
            routes.extend((f'/api/v1/players/{ident}', f'/api/v1/players/{ident}/matches'))
        match_ids = {match['match_id'] for match in data['partidos']} | set(data['excluidos'])
        for ident in sorted(match_ids):
            routes.extend((f'/api/v1/matches/{ident}',
                           *(f'/api/v1/matches/{ident}/{section}' for section in ('statistics', 'lineups', 'incidents')),
                           f'/api/v1/matches/{ident}/unknown-section'))
        routes.extend((
            '/',
            '/api/v1/teams/abc',
            '/api/v1/teams/1',
            '/api/v1/players/not-an-id',
            '/api/v1/players/1',
            '/api/v1/matches/not-an-id',
            '/api/v1/matches/1',
            '/api/v1/matches/1001/not-a-section',
            '/api/v1/unknown/1',
            '/api/v1/teams/2841/',
            '/api/v1/matches/1012',
            '/api/v1/matches/1006',
            '/api/v1/teams/2841/matches?unsupported=true',
        ))
        return list(dict.fromkeys(routes))

    def test_complete_responses_and_relevant_headers_match(self):
        routes = self.routes()
        self.assertGreaterEqual(len(routes), 135)
        for route in routes:
            with self.subTest(route=route):
                old = _get(self.legacy_url, route)
                new = _get(self.fastapi_url, route)
                self.assertEqual(new[:2], old[:2])
                self.assert_json_equal(old[2], new[2], route)

    def assert_json_equal(self, expected, actual, route, location='$'):
        self.assertIs(type(actual), type(expected), f'{route} {location}: tipo JSON distinto')
        if isinstance(expected, dict):
            self.assertEqual(set(actual), set(expected), f'{route} {location}: claves distintas')
            for key in expected:
                self.assert_json_equal(expected[key], actual[key], route, f'{location}.{key}')
        elif isinstance(expected, list):
            self.assertEqual(len(actual), len(expected), f'{route} {location}: longitud distinta')
            for index, (old_item, new_item) in enumerate(zip(expected, actual)):
                self.assert_json_equal(old_item, new_item, route, f'{location}[{index}]')
        else:
            self.assertEqual(actual, expected, f'{route} {location}: valor distinto')


class FastAPISQLLifecycle(BaseCarga):
    """The ASGI lifespan uses one SQL snapshot and owns the existing cooperative lock."""

    def test_loads_sql_once_and_keeps_the_lock_until_shutdown(self):
        from persistencia.bloqueo import BaseEnUso, bloquear
        from persistencia.carga import cargar

        cargar(self.importacion(), self.base)
        with mock.patch.dict(os.environ, {
            'SPORTS_DATA_STORAGE': 'sql',
            'SPORTS_DATA_DB': str(self.base),
            'SPORTS_DATA_SCOPE': str(AMBITO),
        }, clear=False):
            application = fastapi_entry.create_app()

            async def exercise_lifespan():
                async with application.router.lifespan_context(application):
                    self.assertTrue(application.state.data['partidos'])

                    def try_acquire_from_another_thread():
                        with bloquear(self.base):
                            pass

                    with self.assertRaises(BaseEnUso):
                        await asyncio.to_thread(try_acquire_from_another_thread)

            asyncio.run(exercise_lifespan())

        with bloquear(self.base):
            self.assertTrue(self.base.is_file())


if __name__ == '__main__':
    unittest.main()
