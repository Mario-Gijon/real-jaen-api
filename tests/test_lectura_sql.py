"""Lectura de la API desde la base SQLite (fase 3, docs/persistencia-sqlite.md 5.1).

Equivalencia de todas las rutas entre modo JSON y modo SQL con casos sintéticos (conflictos, valores inválidos,
ausencias, sustituciones), errores de arranque y una muestra por HTTP en un puerto libre. Bases temporales fuera del
repositorio. Sin requirements-sql.txt se omiten, salvo la comprobación del mensaje del modo SQL sin dependencias.
"""
import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))
CON_SQL = importlib.util.find_spec('sqlalchemy') is not None and importlib.util.find_spec('alembic') is not None

import api  # noqa: E402
from test_carga import AMBITO, Base as BaseCarga, respuestas  # noqa: E402

if CON_SQL:
    from alembic import command

    from persistencia.carga import cargar, configuracion_alembic
    from persistencia.decisiones import cargar_sustituciones
    from persistencia.lectura import BaseNoUtilizable, cargar_desde_base


def arrancar(*argumentos, timeout=60):
    """api.py con esos argumentos, para los casos en que no debe llegar a escuchar."""
    return subprocess.run([sys.executable, '-B', str(RAIZ / 'api.py'), *map(str, argumentos)], capture_output=True,
                          text=True, encoding='utf-8', timeout=timeout, env=dict(os.environ, PYTHONUTF8='1'))


@unittest.skipIf(CON_SQL, 'solo sin requirements-sql.txt')
class SinDependenciasSQL(unittest.TestCase):
    def test_el_modo_sql_lo_dice_y_no_arranca(self):
        salida = arrancar('--almacen', 'sql', '--base', RAIZ / 'no-existe.sqlite', '--port', '1')
        self.assertEqual(salida.returncode, 1)
        self.assertIn('requirements-sql.txt', salida.stderr)


@unittest.skipUnless(CON_SQL, 'requiere requirements-sql.txt (.venv)')
class EquivalenciaJsonSql(BaseCarga):
    def test_todas_las_rutas_iguales_con_conflictos_invalidos_y_ausencias(self):
        carpeta = self.importacion()
        cargar(carpeta, self.base)
        self.assertEqual(respuestas(cargar_desde_base(self.base, AMBITO)), respuestas(api.cargar(carpeta, AMBITO)))

    def test_todas_las_rutas_iguales_con_sustituciones_leidas_de_la_base(self):
        carpeta = self.importacion()
        cargar(carpeta, self.base)
        archivo = self.sustituciones((300, 301))
        cargar_sustituciones(self.base, archivo, AMBITO)
        sql = respuestas(cargar_desde_base(self.base, AMBITO))
        self.assertEqual(sql, respuestas(api.cargar(carpeta, AMBITO, archivo)))
        # El evento sustituido sigue servido, con su decisión; el calendario del equipo solo lleva el vigente.
        detalle = json.loads(next(t for r, _, t in sql if r == '/api/v1/matches/300'))
        self.assertEqual(detalle['data']['replaced_by']['match_id'], 301)

    def test_normalizacion_y_evidencia_desde_la_base(self):
        carpeta = self.importacion()
        cargar(carpeta, self.base)
        sql, json_ = cargar_desde_base(self.base, AMBITO), api.cargar(carpeta, AMBITO)
        # Participación, cifras, normalized.stats, incidencias y evidencia: idénticas en los dos modos.
        self.assertEqual(api.actuaciones_del_partido(sql, 200), api.actuaciones_del_partido(json_, 200))
        jugadores = api.actuaciones_del_partido(sql, 200)
        motivo = lambda j, m: jugadores[j]['stats'][m]['reason'] or jugadores[j]['stats'][m]['source']
        # Versiones de la alineación que discrepan en un grupo: sin dato en ese grupo, con su evidencia.
        self.assertEqual((motivo(3, 'shots'), motivo(3, 'passes')), ('version_conflict', 'reported'))
        self.assertTrue(any(e['issue'] == 'stat_version_conflict' for e in jugadores[3]['evidence']))
        # No utilizado, participación sin resolver y ausencias: nunca un cero inventado.
        self.assertEqual((motivo(2, 'fouls'), motivo(5, 'shots'), motivo(1, 'fouls')),
                         ('unused', 'participation_unknown', 'not_reported'))


@unittest.skipUnless(CON_SQL, 'requiere requirements-sql.txt (.venv)')
class ErroresDeArranque(BaseCarga):
    def comprobar(self, salida, codigo, texto):
        self.assertEqual(salida.returncode, codigo, salida.stderr)
        self.assertIn(texto, salida.stderr)

    def test_combinaciones_de_argumentos(self):
        self.comprobar(arrancar('--almacen', 'sql', '--port', '1'), 2, '--almacen sql necesita --base')
        self.comprobar(arrancar('--almacen', 'sql', '--base', self.base, '--datos', self.tmp), 2, 'no admite --datos')
        self.comprobar(arrancar('--almacen', 'sql', '--base', self.base, '--sustituciones', self.tmp / 's.json'), 2,
                       'no admite --datos ni --sustituciones')
        self.comprobar(arrancar('--base', self.base), 2, '--base solo se usa con --almacen sql')
        self.comprobar(arrancar('--almacen', 'otro'), 2, 'invalid choice')

    def test_base_inexistente_no_se_crea(self):
        self.comprobar(arrancar('--almacen', 'sql', '--base', self.base, '--port', '1'), 1, 'No existe la base')
        self.assertFalse(self.base.exists())
        self.assertFalse(self.base.parent.exists())

    def test_archivo_que_no_es_una_base(self):
        self.base.parent.mkdir(parents=True)
        self.base.write_text('no es una base', encoding='utf-8')
        self.comprobar(arrancar('--almacen', 'sql', '--base', self.base, '--port', '1'), 1, 'No se puede leer')
        self.assertEqual(self.base.read_text(encoding='utf-8'), 'no es una base')

    def test_base_sqlite_ajena_o_de_otra_revision(self):
        self.base.parent.mkdir(parents=True)
        with sqlite3.connect(self.base) as c:
            c.execute('CREATE TABLE otra (x INTEGER)')
        self.comprobar(arrancar('--almacen', 'sql', '--base', self.base, '--port', '1'), 1, 'sin revisión de esquema')
        cargar(self.importacion(), self.tmp / 'valida.sqlite')
        shutil.copy(self.tmp / 'valida.sqlite', self.tmp / 'antigua.sqlite')
        with sqlite3.connect(self.tmp / 'antigua.sqlite') as c:
            c.execute("UPDATE alembic_version SET version_num = '0001'")
        with self.assertRaisesRegex(BaseNoUtilizable, 'revisión de esquema 0001; este código necesita 0004'):
            cargar_desde_base(self.tmp / 'antigua.sqlite', AMBITO)

    def test_base_sin_carga_inicial(self):
        self.base.parent.mkdir(parents=True)
        command.upgrade(configuracion_alembic(self.base), 'head')
        with self.assertRaisesRegex(BaseNoUtilizable, 'carga inicial confirmada'):
            cargar_desde_base(self.base, AMBITO)

    def test_sustituciones_guardadas_fuera_del_ambito(self):
        cargar(self.importacion(), self.base)
        cargar_sustituciones(self.base, self.sustituciones((300, 301)), AMBITO)
        ambito = self.tmp / 'otro-ambito.json'
        ambito.write_text(json.dumps({'competiciones': [{'nombre': 'Otro', 'unique_tournament_id': 1, 'tournament_id': 2,
                                                         'season_id': 3}]}), encoding='utf-8')
        self.comprobar(arrancar('--almacen', 'sql', '--base', self.base, '--ambito', ambito, '--port', '1'), 1,
                       'fuera del ámbito')


@unittest.skipUnless(CON_SQL, 'requiere requirements-sql.txt (.venv)')
class MuestraPorHTTP(BaseCarga):
    def test_el_servidor_en_modo_sql_responde_igual_que_el_modo_json(self):
        carpeta = self.importacion()
        cargar(carpeta, self.base)
        archivo = self.sustituciones((300, 301))
        cargar_sustituciones(self.base, archivo, AMBITO)
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            puerto = s.getsockname()[1]
        proceso = subprocess.Popen([sys.executable, '-B', str(RAIZ / 'api.py'), '--almacen', 'sql', '--base', str(self.base),
                                    '--port', str(puerto)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            limite = time.monotonic() + 30
            while True:
                try:
                    urllib.request.urlopen(f'http://127.0.0.1:{puerto}/health', timeout=1).close()
                    break
                except OSError:
                    if proceso.poll() is not None or time.monotonic() > limite:
                        raise RuntimeError(f'La API en modo SQL no arrancó: {proceso.stderr.read()}')
                    time.sleep(0.2)
            esperadas = {r: (e, t) for r, e, t in respuestas(api.cargar(carpeta, AMBITO, archivo))}
            muestra = ['/api/v1/scope', '/api/v1/teams/2841/matches', '/api/v1/teams/2841/performance',
                       '/api/v1/teams/2841/player-stats', '/api/v1/players/1/matches', '/api/v1/matches/200/lineups',
                       '/api/v1/matches/300', '/api/v1/matches/400', '/api/v1/teams/1', '/api/v1/teams/abc']
            for ruta in muestra:
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{puerto}{ruta}', timeout=10) as r:
                        estado, texto = r.status, r.read().decode('utf-8')
                except urllib.error.HTTPError as e:
                    with e:
                        estado, texto = e.code, e.read().decode('utf-8')
                self.assertEqual((estado, texto), esperadas[ruta], ruta)
        finally:
            proceso.terminate()
            proceso.wait(timeout=10)
            proceso.stderr.close()


if __name__ == '__main__':
    unittest.main()
