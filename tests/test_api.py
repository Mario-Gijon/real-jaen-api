"""Pruebas de la API con la muestra sintética de tests/fixtures/datos (solo biblioteca estándar).

No necesitan la exportación real ni el servicio del puerto 8001: usan el ambito.json del
proyecto y arrancan api.py en un puerto libre para las comprobaciones HTTP.
Ejecutar desde la carpeta de la API:  python -m unittest discover -s tests -v
Para regenerar la muestra:  python tests/fixtures/crear.py
"""
import json
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'fixtures'))
import api  # noqa: E402
import crear  # noqa: E402

MUESTRA = Path(__file__).resolve().parent / 'fixtures' / 'datos'
DATOS = api.cargar(MUESTRA)
SECCIONES = ('statistics', 'lineups', 'incidents')


def consultar(ruta):
    return api.consultar(DATOS, ruta)


class Rutas(unittest.TestCase):
    def test_health_y_equipo(self):
        self.assertEqual(consultar('/health'), (200, {'data': {'status': 'ok'}}))
        self.assertEqual(consultar(f'/api/v1/teams/{crear.JAEN}')[1]['data']['name'], 'Real Jaén')

    def test_errores(self):
        self.assertEqual(consultar('/api/v1/teams/1')[0], 404)
        self.assertEqual(consultar('/api/v1/players/abc')[0], 400)
        self.assertEqual(consultar(f'/api/v1/teams/{crear.JAEN}/otra')[0], 404)
        self.assertEqual(consultar('/api/v1/otra/1')[0], 404)
        self.assertEqual(consultar('/api/v1/matches/1/otra'), (404, {'error': 'Partido no encontrado'}))

    def test_partido_y_secciones(self):
        estado, cuerpo = consultar(f'/api/v1/matches/{crear.FINALIZADO}')
        self.assertEqual((estado, cuerpo['data']['home_team_name']), (200, 'Club Sintético A'))
        self.assertEqual(cuerpo['meta']['sections'], {s: 'stored' for s in SECCIONES})
        estado, cuerpo = consultar(f'/api/v1/matches/{crear.FINALIZADO}/lineups')
        self.assertEqual((estado, cuerpo['meta']['availability'], cuerpo['meta']['versions']), (200, 'stored', 1))
        # Un partido pendiente existe sin secciones y sin marcador inventado.
        estado, cuerpo = consultar(f'/api/v1/matches/{crear.PENDIENTE}')
        self.assertEqual((cuerpo['data']['home_score'], cuerpo['meta']['sections']), (None, {s: 'not_stored' for s in SECCIONES}))
        self.assertEqual(consultar(f'/api/v1/matches/{crear.PENDIENTE}/statistics')[1]['data'], None)


class Ambito(unittest.TestCase):
    """Sólo Primera Federación, Grupo 2 (26/27), según el ambito.json del proyecto."""

    def test_el_ambito_del_proyecto_se_publica(self):
        self.assertEqual(consultar('/api/v1/scope')[1]['data']['competitions'], [
            {'name': 'Primera Federación, Grupo 2', 'season': '26/27',
             'unique_tournament_id': 17073, 'tournament_id': 94191, 'season_id': 97382}])

    def test_solo_partidos_del_grupo_2(self):
        partidos = consultar(f'/api/v1/teams/{crear.JAEN}/matches')[1]['data']
        self.assertEqual([m['match_id'] for m in partidos], [crear.FINALIZADO, crear.PENDIENTE])

    def test_copa_y_otro_grupo_no_se_sirven_ni_por_su_identificador(self):
        # El Grupo 1 comparte competición general (17073) y temporada: no basta con ellas.
        for ident in (crear.EN_COPA, crear.EN_GRUPO_1):
            for ruta in (f'/api/v1/matches/{ident}', *(f'/api/v1/matches/{ident}/{s}' for s in SECCIONES)):
                self.assertEqual(consultar(ruta), (404, {'error': 'Partido fuera del ámbito de esta versión'}), ruta)

    def test_jugadores_y_convocatorias_solo_del_ambito(self):
        cuerpo = consultar(f'/api/v1/teams/{crear.JAEN}/players')[1]
        self.assertEqual(sorted(p['player_id'] for p in cuerpo['data']), [crear.UNO, crear.SUPLENTE])
        self.assertEqual(consultar(f'/api/v1/players/{crear.SOLO_COPA}'), (404, {'error': 'Jugador fuera del ámbito de esta versión'}))
        partidos = consultar(f'/api/v1/players/{crear.UNO}/matches')[1]['data']
        self.assertEqual([r['match_id'] for r in partidos], [crear.FINALIZADO])

    def test_la_ficha_no_sale_de_un_partido_excluido(self):
        # La ficha guardada es la de Copa (más reciente): se rehace con la alineación del Grupo 2.
        ficha = consultar(f'/api/v1/players/{crear.UNO}')[1]['data']
        self.assertEqual((ficha['name'], ficha['usual_position'], ficha['height_cm'], ficha['birth_date']),
                         ('Jugador Sintético Uno', 'M', 175, '2000-01-01'))
        self.assertEqual(ficha['fetched_at'], '2026-10-21T12:00:00+00:00')
        # Sin convocatorias excluidas, la ficha guardada se sirve tal cual.
        self.assertEqual(consultar(f'/api/v1/players/{crear.SUPLENTE}')[1]['data']['name'], 'Jugador Sintético Suplente')

    def test_los_datos_cargados_conservan_los_excluidos(self):
        # Excluir no es borrar: lo excluido sigue en los JSON de origen.
        partidos = json.loads((MUESTRA / 'partidos.json').read_text(encoding='utf-8'))
        self.assertEqual(len(partidos), 4)
        self.assertEqual(DATOS['excluidos'], {crear.EN_COPA, crear.EN_GRUPO_1})


class ServidorHTTP(unittest.TestCase):
    """Arranca api.py de verdad con la muestra sintética y consulta por HTTP."""

    @classmethod
    def setUpClass(cls):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            cls.puerto = s.getsockname()[1]
        cls.proceso = subprocess.Popen([sys.executable, str(RAIZ / 'api.py'), '--datos', str(MUESTRA), '--port', str(cls.puerto)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        limite = time.monotonic() + 10
        while time.monotonic() < limite:
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{cls.puerto}/health', timeout=1).close()
                return
            except OSError:
                time.sleep(0.1)
        cls.proceso.kill()
        raise RuntimeError('La API no arrancó')

    @classmethod
    def tearDownClass(cls):
        cls.proceso.terminate()
        cls.proceso.wait(timeout=10)

    def get(self, ruta):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{self.puerto}{ruta}', timeout=5) as r:
                return r.status, r.headers['Content-Type'], json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers['Content-Type'], json.loads(e.read().decode('utf-8'))

    def test_rutas_por_http(self):
        for ruta, estado in (('/health', 200), ('/api/v1/scope', 200), (f'/api/v1/teams/{crear.JAEN}/matches', 200),
                             (f'/api/v1/teams/{crear.JAEN}/players', 200), (f'/api/v1/players/{crear.UNO}/matches', 200),
                             (f'/api/v1/matches/{crear.FINALIZADO}/lineups', 200), (f'/api/v1/matches/{crear.EN_COPA}', 404),
                             (f'/api/v1/matches/{crear.EN_COPA}/lineups', 404), ('/api/v1/teams/abc', 400),
                             (f'/api/v1/teams/{crear.JAEN}?x=1', 400)):
            recibido, tipo, _ = self.get(ruta)
            self.assertEqual(recibido, estado, ruta)
            self.assertEqual(tipo, 'application/json; charset=utf-8')


if __name__ == '__main__':
    unittest.main()
