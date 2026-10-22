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
        self.assertEqual({m['tournament_id'] for m in partidos}, {94191})
        self.assertNotIn(crear.EN_COPA, {m['match_id'] for m in partidos})
        self.assertNotIn(crear.EN_GRUPO_1, {m['match_id'] for m in partidos})
        self.assertEqual(len(partidos), 10)

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
        self.assertEqual(len(partidos), 12)
        self.assertEqual(DATOS['excluidos'], {crear.EN_COPA, crear.EN_GRUPO_1})


class Rendimiento(unittest.TestCase):
    """Balance del equipo con los finalizados del ámbito con marcador válido; sin clasificación ni puntos."""

    def competicion(self, equipo=crear.JAEN):
        estado, cuerpo = consultar(f'/api/v1/teams/{equipo}/performance')
        self.assertEqual(estado, 200)
        self.assertIn('no es necesariamente la temporada completa', cuerpo['meta']['note'])
        [competicion] = cuerpo['data']['competitions']
        return competicion

    def test_una_entrada_por_competicion_del_ambito_con_sus_ids(self):
        c = self.competicion()
        self.assertEqual({k: c[k] for k in ('name', 'season', 'unique_tournament_id', 'tournament_id', 'season_id')},
                         {'name': 'Primera Federación, Grupo 2', 'season': '26/27',
                          'unique_tournament_id': 17073, 'tournament_id': 94191, 'season_id': 97382})
        self.assertNotIn('points', c['record'])
        self.assertNotIn('rank', c)

    def test_recuentos_por_estado(self):
        # Copa y Grupo 1, aunque finalizados y con marcador, no están en el ámbito.
        self.assertEqual(self.competicion()['matches'], {
            'in_scope': 10, 'finished': 6, 'pending': 3, 'counted': 3,
            'finished_without_valid_score': 3, 'unknown_status': 1})

    def test_balance_casa_fuera_y_diferencia(self):
        c = self.competicion()
        self.assertEqual(c['record'], {'played': 3, 'wins': 1, 'draws': 1, 'losses': 1,
                                       'goals_for': 3, 'goals_against': 4, 'goal_difference': -1})
        self.assertEqual(c['venues'], {'home': {'wins': 0, 'draws': 1, 'losses': 1}, 'away': {'wins': 1, 'draws': 0, 'losses': 0}})

    def test_resultados_por_fecha_desde_el_lado_del_equipo(self):
        resultados = self.competicion()['results']
        self.assertEqual([(r['match']['match_id'], r['side'], r['opponent_team_id'], r['goals_for'], r['goals_against'], r['result'])
                          for r in resultados],
                         [(crear.FINALIZADO, 'away', crear.RIVAL_A, 2, 0, 'win'),
                          (crear.EMPATE_CASA, 'home', crear.RIVAL_B, 1, 1, 'draw'),
                          (crear.DERROTA_CASA, 'home', crear.RIVAL_A, 0, 3, 'loss')])
        # Ningún marcador inválido, pendiente o en juego entra como resultado (ni como 0-0).
        excluidos = {crear.SIN_MARCADOR, crear.MARCADOR_BOOLEANO, crear.MARCADOR_NEGATIVO, crear.EN_JUEGO,
                     crear.PENDIENTE_CON_MARCADOR, crear.APLAZADO, crear.PENDIENTE}
        self.assertFalse(excluidos & {r['match']['match_id'] for r in resultados})

    def test_marcador_valido(self):
        for valor, valido in ((0, True), (3, True), (True, False), (False, False), (-1, False), (1.0, False), ('1', False), (None, False)):
            self.assertIs(api.marcador_valido(valor), valido, repr(valor))

    def test_equipo_sin_partidos_en_el_ambito_y_equipo_desconocido(self):
        # Sólo jugó en Copa: cero partidos, que no es un error.
        c = self.competicion(crear.RIVAL_COPA)
        self.assertEqual((c['matches']['in_scope'], c['record']['played'], c['results']), (0, 0, []))
        self.assertEqual(consultar('/api/v1/teams/1/performance'), (404, {'error': 'Entidad no encontrada'}))


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
                             (f'/api/v1/teams/{crear.JAEN}/performance', 200), ('/api/v1/teams/1/performance', 404),
                             (f'/api/v1/matches/{crear.FINALIZADO}/lineups', 200), (f'/api/v1/matches/{crear.EN_COPA}', 404),
                             (f'/api/v1/matches/{crear.EN_COPA}/lineups', 404), ('/api/v1/teams/abc', 400),
                             (f'/api/v1/teams/{crear.JAEN}?x=1', 400)):
            recibido, tipo, _ = self.get(ruta)
            self.assertEqual(recibido, estado, ruta)
            self.assertEqual(tipo, 'application/json; charset=utf-8')


if __name__ == '__main__':
    unittest.main()
