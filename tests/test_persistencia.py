"""Esquema de persistencia (fase 1, docs/persistencia-sqlite.md): migraciones, conexión SQLite y restricciones.

Solo bases temporales. Requieren requirements-sql.txt (entorno .venv); sin SQLAlchemy se omiten, y el resto de la
suite (API e importador JSON) sigue funcionando sin esas dependencias.
  .venv\\Scripts\\python -m unittest discover -s tests -v
"""
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
CON_SQL = importlib.util.find_spec('sqlalchemy') is not None and importlib.util.find_spec('alembic') is not None

if CON_SQL:
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError, OperationalError

    from persistencia import modelos, sqlite

TABLAS = {
    'competition', 'season', 'stage', 'team', 'player', 'match',
    'competition_external_id', 'season_external_id', 'stage_external_id', 'team_external_id', 'player_external_id',
    'match_external_id', 'identity_decision', 'identity_decision_subject',
    'import_run', 'source_file', 'import_run_file', 'file_team_declaration', 'content', 'observation',
    'rule_set', 'section_selection', 'section_selection_history', 'discrepancy', 'discrepancy_reason',
    'discrepancy_observation', 'discrepancy_value', 'match_state', 'match_version_issue', 'team_name', 'call_up',
    'call_up_stat', 'team_match_stat', 'incident', 'player_attribute_observation', 'local_replacement',
}
H1, H2 = 'a' * 64, 'b' * 64


def configuracion(ruta):
    c = Config(str(RAIZ / 'alembic.ini'))
    c.attributes['base'] = str(ruta)
    c.attributes['configurar_registro'] = False
    return c


def normalizar(tipo, sql):
    """Una tabla: sus columnas en orden y el conjunto exacto de restricciones (autogenerate las escribe en otro
    orden). Un índice: su SQL tal cual."""
    if tipo != 'table':
        return sql
    lineas = [l.strip().rstrip(',').strip() for l in sql[sql.index('(') + 1:sql.rindex(')')].splitlines() if l.strip()]
    return (tuple(l for l in lineas if not l.startswith('CONSTRAINT')),
            frozenset(l for l in lineas if l.startswith('CONSTRAINT')))


def esquema(ruta):
    """Definiciones de tablas e índices de la aplicación, tal como las guarda SQLite (normalizadas)."""
    motor = sqlite.crear_motor(ruta)
    try:
        with motor.connect() as c:
            filas = c.exec_driver_sql("SELECT type, name, tbl_name, sql FROM sqlite_master "
                                      "WHERE name NOT LIKE 'sqlite_%' AND tbl_name <> 'alembic_version'").fetchall()
    finally:
        motor.dispose()
    return {(t, n, tabla, normalizar(t, sql)) for t, n, tabla, sql in filas}


def uid():
    return str(uuid.uuid4())


@unittest.skipUnless(CON_SQL, 'requiere requirements-sql.txt (.venv)')
class Migraciones(unittest.TestCase):
    def setUp(self):
        self.carpeta = Path(tempfile.mkdtemp(prefix='rj-persistencia-'))
        self.addCleanup(shutil.rmtree, self.carpeta, True)

    def test_upgrade_downgrade_y_upgrade_de_nuevo(self):
        base = self.carpeta / 'm.sqlite'
        command.upgrade(configuracion(base), 'head')
        primero = esquema(base)
        self.assertEqual({n for t, n, *_ in primero if t == 'table'}, TABLAS)
        motor = sqlite.crear_motor(base)
        with motor.connect() as c:
            self.assertEqual(c.exec_driver_sql('SELECT version_num FROM alembic_version').scalar(), '0001')
        motor.dispose()
        command.downgrade(configuracion(base), 'base')
        self.assertEqual(esquema(base), set())
        command.upgrade(configuracion(base), 'head')
        self.assertEqual(esquema(base), primero)

    def test_la_migracion_crea_exactamente_lo_que_definen_los_modelos(self):
        migrada, modelada = self.carpeta / 'migrada.sqlite', self.carpeta / 'modelada.sqlite'
        command.upgrade(configuracion(migrada), 'head')
        motor = sqlite.crear_motor(modelada)
        modelos.Base.metadata.create_all(motor)
        motor.dispose()
        # Mismo DDL, restricciones CHECK incluidas (autogenerate no las compara).
        self.assertEqual(esquema(migrada), esquema(modelada))
        motor = sqlite.crear_motor(migrada)
        with motor.connect() as c:
            diferencias = compare_metadata(MigrationContext.configure(c, opts={'compare_type': True,
                                                                               'compare_server_default': True}),
                                           modelos.Base.metadata)
        motor.dispose()
        self.assertEqual(diferencias, [])

    def test_sin_base_indicada_no_migra(self):
        c = Config(str(RAIZ / 'alembic.ini'))
        c.attributes['configurar_registro'] = False
        with self.assertRaises(SystemExit):
            command.upgrade(c, 'head')


@unittest.skipUnless(CON_SQL, 'requiere requirements-sql.txt (.venv)')
class Base(unittest.TestCase):
    """Cada prueba sobre una copia de una base recién migrada, con una semilla mínima."""

    @classmethod
    def setUpClass(cls):
        cls.plantilla_dir = Path(tempfile.mkdtemp(prefix='rj-persistencia-plantilla-'))
        cls.plantilla = cls.plantilla_dir / 'plantilla.sqlite'
        command.upgrade(configuracion(cls.plantilla), 'head')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.plantilla_dir, True)

    def setUp(self):
        self.carpeta = Path(tempfile.mkdtemp(prefix='rj-persistencia-'))
        self.addCleanup(shutil.rmtree, self.carpeta, True)
        self.base = self.carpeta / 'b.sqlite'
        shutil.copy(self.plantilla, self.base)
        self.motor = sqlite.crear_motor(self.base)
        self.addCleanup(self.motor.dispose)
        with self.motor.begin() as c:
            self.semilla(c)

    def inserta(self, c, tabla, **valores):
        columnas = ', '.join(f'"{k}"' for k in valores)
        c.execute(text(f'INSERT INTO "{tabla}" ({columnas}) VALUES ({", ".join(":" + k for k in valores)})'), valores)

    def acepta(self, tabla, **valores):
        with self.motor.begin() as c:
            self.inserta(c, tabla, **valores)

    def rechaza(self, tabla, **valores):
        with self.assertRaises(IntegrityError):
            with self.motor.begin() as c:
                self.inserta(c, tabla, **valores)

    def valor(self, sql, **parametros):
        with self.motor.connect() as c:
            return c.execute(text(sql), parametros).scalar()

    def semilla(self, c):
        self.inserta(c, 'import_run', id=1, kind='initial_load', started_at='2026-10-24T10:00:00+00:00',
                     finished_at='2026-10-24T10:01:00+00:00', status='committed')
        self.inserta(c, 'rule_set', id=1, name='importar.seleccionar', version='2', code_ref='b9247ce')
        self.inserta(c, 'competition', id=1, uid=uid(), created_in_run=1)
        for tabla in ('team', 'player', 'match'):
            for i in (1, 2):
                self.inserta(c, tabla, id=i, uid=uid(), created_in_run=1)
        self.inserta(c, 'content', hash=H1, payload_text='{"home":{"players":[]},"confirmed":true}', text_sha256=H1, bytes=40)
        self.inserta(c, 'content', hash=H2, payload_text='{"confirmed":false}', text_sha256=H2, bytes=19)
        self.inserta(c, 'source_file', id=1, sha256='c' * 64, kind='raw', label='E1', records=5, first_run_id=1)
        observaciones = [(1, 1, '100', 'lineups', H1), (2, 2, '100', 'lineups', H2), (3, 2, '101', 'lineups', H1),
                         (4, 1, '100', 'event', H2), (5, 1, '100', 'statistics', H2)]
        for ident, partido, externo, clave, huella in observaciones:
            self.inserta(c, 'observation', id=ident, source_file_id=1, line=ident, kind='section', provider='sofascore',
                         external_event_id=externo, key=clave, fetched_at_raw='2026-10-21T12:00:00+00:00',
                         fetched_instant='2026-10-21T12:00:00+00:00', fetched_at_valid=True, content_hash=huella,
                         match_id=partido)
        for clave, observacion in (('lineups', 1), ('event', 4), ('statistics', 5)):
            self.inserta(c, 'section_selection', match_id=1, key=clave, observation_id=observacion, versions=1,
                         rule_set_id=1, run_id=1)

    def convocatoria(self, **cambios):
        valores = dict(match_id=1, lineup_observation_id=1, side='home', entry_index=0, team_id=1,
                       external_player_id='966303', player_id=1, statistics_kind='object', rule_set_id=1, run_id=1)
        valores.update(cambios)
        return valores


class Conexion(Base):
    def test_claves_foraneas_activas_y_comprobadas_en_cada_conexion(self):
        # Tres conexiones nuevas a la vez, sin abrir transacciones (el motor de escritura abre BEGIN IMMEDIATE: dos
        # transacciones a la vez se bloquearían, que es lo esperado de un único escritor).
        self.motor.dispose()
        conexiones = [self.motor.raw_connection() for _ in range(3)]
        try:
            self.assertEqual([c.cursor().execute('PRAGMA foreign_keys').fetchone()[0] for c in conexiones], [1, 1, 1])
        finally:
            for c in conexiones:
                c.close()
        with self.motor.connect() as c:
            self.assertEqual(c.exec_driver_sql('PRAGMA journal_mode').scalar(), 'wal')
            self.assertEqual(sqlite.comprobar_integridad(c), [])
        self.rechaza('team', id=9, uid=uid(), created_in_run=99)

    def test_una_conexion_sin_claves_foraneas_no_se_usa(self):
        class Cursor:
            def execute(self, sql):
                return self

            def fetchone(self):
                return (0,)

            def close(self):
                pass

        class ConexionFalsa:
            isolation_level = ''

            def cursor(self):
                return Cursor()

        with self.assertRaises(sqlite.ClavesForaneasDesactivadas):
            sqlite.preparar_conexion(ConexionFalsa(), solo_lectura=False)

    def test_solo_lectura(self):
        lector = sqlite.crear_motor(self.base, solo_lectura=True)
        try:
            with lector.connect() as c:
                self.assertEqual(c.exec_driver_sql('SELECT count(*) FROM team').scalar(), 2)
                self.assertEqual(sqlite.claves_foraneas_activas(c), 1)
            with self.assertRaises(OperationalError):
                with lector.begin() as c:
                    self.inserta(c, 'team', id=3, uid=uid(), created_in_run=1)
        finally:
            lector.dispose()

    def test_un_solo_escritor_desde_el_principio_y_rollback_completo(self):
        anterior = sqlite.ESPERA_BLOQUEO_MS
        sqlite.ESPERA_BLOQUEO_MS = 50
        otro = sqlite.crear_motor(self.base)
        self.addCleanup(otro.dispose)
        sqlite.ESPERA_BLOQUEO_MS = anterior
        with self.motor.begin():
            # BEGIN IMMEDIATE: el segundo escritor no puede ni empezar.
            with self.assertRaises(OperationalError):
                with otro.begin():
                    pass
        with self.assertRaises(IntegrityError):
            with sqlite.transaccion_de_escritura(self.motor) as c:
                self.inserta(c, 'team', id=3, uid=uid(), created_in_run=1)
                self.inserta(c, 'team', id=4, uid='corto', created_in_run=1)
        self.assertEqual(self.valor('SELECT count(*) FROM team'), 2)


class Identidad(Base):
    def externo(self, **cambios):
        valores = dict(provider='sofascore', external_id='966303', entity_id=1, status='origin', created_in_run=1)
        valores.update(cambios)
        return valores

    def test_unicidad_por_proveedor_espacio_e_id_entre_los_vivos(self):
        self.acepta('player_external_id', **self.externo())
        self.rechaza('player_external_id', **self.externo(entity_id=2))
        self.acepta('identity_decision', id=1, uid=uid(), kind='reject_external_id', entity_type='player',
                    reason='id duplicado', decided_by='analista', decided_on='2026-10-24', evidence='prueba', run_id=1)
        # Un rechazado no ocupa el id; otro espacio de nombres u otro proveedor tampoco chocan.
        self.acepta('player_external_id', **self.externo(entity_id=2, status='rejected', decision_id=1))
        self.acepta('player_external_id', **self.externo(entity_id=2, status='verified', decision_id=1,
                                                         namespace='17073', external_id='966303-x'))
        self.acepta('player_external_id', **self.externo(entity_id=2, status='verified', decision_id=1, provider='besoccer'))
        # Los ids externos son texto: '0966303' no es '966303'.
        self.acepta('player_external_id', **self.externo(entity_id=2, status='verified', decision_id=1, external_id='0966303'))

    def test_un_solo_origen_y_asociaciones_con_decision(self):
        self.acepta('player_external_id', **self.externo())
        self.rechaza('player_external_id', **self.externo(external_id='111'))
        self.rechaza('player_external_id', **self.externo(entity_id=2, external_id='222', status='verified'))
        self.rechaza('player_external_id', **self.externo(entity_id=2, external_id=' '))
        self.rechaza('player_external_id', **self.externo(entity_id=2, external_id='333', status='otro'))

    def test_fusion_sin_perdida(self):
        self.rechaza('team', id=3, uid=uid(), status='merged', created_in_run=1)
        self.rechaza('team', id=3, uid=uid(), status='active', merged_into_id=1, created_in_run=1)
        self.rechaza('team', id=3, uid=uid(), status='merged', merged_into_id=3, created_in_run=1)
        self.rechaza('team', id=3, uid=uid(), status='merged', merged_into_id=99, created_in_run=1)
        self.acepta('team', id=3, uid=uid(), status='merged', merged_into_id=1, created_in_run=1)
        self.rechaza('team', id=4, uid=self.valor('SELECT uid FROM team WHERE id = 3'), created_in_run=1)

    def test_decisiones_y_sujetos(self):
        decision = dict(uid=uid(), kind='merge', entity_type='team', reason='mismo club', decided_by='analista',
                        decided_on='2026-10-24', evidence='fichas', run_id=1)
        self.rechaza('identity_decision', id=1, **dict(decision, kind='revert'))
        self.rechaza('identity_decision', id=1, **dict(decision, entity_type='referee'))
        self.rechaza('identity_decision', id=1, **dict(decision, evidence='  '))
        self.acepta('identity_decision', id=1, **decision)
        self.rechaza('identity_decision', id=2, **dict(decision, uid=uid(), reverts_decision_id=1))
        self.acepta('identity_decision', id=2, **dict(decision, uid=uid(), kind='revert', reverts_decision_id=1))
        self.rechaza('identity_decision_subject', decision_id=1, ordinal=0, role='source')
        self.rechaza('identity_decision_subject', decision_id=1, ordinal=0, role='source', team_id=1, player_id=1)
        self.acepta('identity_decision_subject', decision_id=1, ordinal=0, role='source', team_id=2)
        self.acepta('identity_decision_subject', decision_id=1, ordinal=1, role='target', team_id=1)


class Evidencia(Base):
    def test_fichero_por_contenido(self):
        self.rechaza('source_file', id=2, sha256='c' * 64, kind='raw', label='E2', records=0, first_run_id=1)
        self.rechaza('source_file', id=2, sha256='D' * 64, kind='raw', label='E2', records=0, first_run_id=1)
        self.rechaza('source_file', id=2, sha256='d' * 63, kind='raw', label='E2', records=0, first_run_id=1)
        self.rechaza('source_file', id=2, sha256='d' * 64, kind='raw', label='E1', records=0, first_run_id=1)
        self.acepta('source_file', id=2, sha256='d' * 64, kind='player_profile', label='E2', records=0, first_run_id=1)
        # El mismo fichero presentado otra vez: otra fila de presentación, ninguna observación nueva.
        self.acepta('import_run', id=2, kind='import', started_at='x', finished_at='y', status='no_changes')
        self.acepta('import_run_file', run_id=2, source_file_id=1, position=1, presented_name='otro-nombre.jsonl',
                    outcome='already_known')
        self.rechaza('import_run_file', run_id=2, source_file_id=2, position=1, presented_name='x', outcome='ingested')

    def test_declaraciones_auditadas(self):
        declaracion = dict(source_file_id=1, team_id=1, external_team_id='2841', status='active', declared_in_run=1)
        self.acepta('file_team_declaration', id=1, **declaracion)
        self.rechaza('file_team_declaration', id=2, **declaracion)
        self.acepta('file_team_declaration', id=2, **dict(declaracion, team_id=2, external_team_id='24368'))
        self.rechaza('file_team_declaration', id=3, **dict(declaracion, status='withdrawn'))
        with self.motor.begin() as c:
            c.exec_driver_sql("UPDATE file_team_declaration SET status = 'withdrawn', withdrawn_in_run = 1, "
                              "withdrawn_by = 'analista', withdrawn_reason = 'no era propia' WHERE id = 1")
        self.acepta('file_team_declaration', id=3, **declaracion)
        self.assertEqual(self.valor('SELECT count(*) FROM file_team_declaration'), 3)

    def test_contenido_json_con_tipos_orden_y_sin_inventar(self):
        original = '{"z":1,"a":true,"n":null,"s":"4","f":1.5,"neg":-1}'
        self.acepta('content', hash='e' * 64, payload_text=original, text_sha256='e' * 64, bytes=len(original))
        self.assertEqual(self.valor("SELECT payload_text FROM content WHERE hash = :h", h='e' * 64), original)
        for invalido in ('{"a": NaN}', '{"a":', 'Infinity'):
            self.rechaza('content', hash='f' * 64, payload_text=invalido, text_sha256='f' * 64, bytes=1)

    def test_observaciones_contradictorias_e_invalidas_se_guardan(self):
        # Otra versión de la misma sección con contenido distinto y fecha inválida: es evidencia, se guarda.
        self.acepta('observation', id=6, source_file_id=1, line=6, kind='section', provider='sofascore',
                    external_event_id='100', key='lineups', fetched_at_raw='sin-fecha', fetched_at_valid=False,
                    content_hash=H2, match_id=1)
        self.acepta('observation', id=7, source_file_id=1, line=7, kind='listing', provider='sofascore',
                    external_event_id='300', key='listing', fetched_at_valid=False, content_hash=H2)
        base = dict(source_file_id=1, provider='sofascore', content_hash=H1, fetched_at_valid=False)
        self.rechaza('observation', id=8, line=1, kind='section', external_event_id='100', key='event', **base)
        self.rechaza('observation', id=8, line=8, kind='section', external_event_id='100', key='event',
                     **dict(base, fetched_at_valid=True))
        self.rechaza('observation', id=8, line=8, kind='listing', external_event_id='100', key='event', **base)
        self.rechaza('observation', id=8, line=8, kind='section', key='event', **base)
        self.rechaza('observation', id=8, line=8, kind='player_profile', key='player_profile', **base)
        self.acepta('observation', id=8, line=8, kind='player_profile', key='player_profile', external_player_id='1',
                    endpoint='/player/1', **base)
        self.rechaza('observation', id=9, line=9, kind='section', external_event_id='100', key='event',
                     envelope_extra='no es json', **base)


class EstadoOperativo(Base):
    def test_la_seleccion_solo_apunta_a_una_observacion_de_su_seccion(self):
        self.rechaza('section_selection', match_id=2, key='lineups', observation_id=1, versions=1, rule_set_id=1, run_id=1)
        self.rechaza('section_selection', match_id=2, key='event', observation_id=3, versions=1, rule_set_id=1, run_id=1)
        self.rechaza('section_selection', match_id=1, key='lineups', observation_id=2, versions=1, rule_set_id=1, run_id=1)
        self.acepta('section_selection', match_id=2, key='lineups', observation_id=3, versions=1, rule_set_id=1, run_id=1)

    def test_una_convocatoria_solo_sale_de_la_alineacion_seleccionada(self):
        self.acepta('call_up', id=1, **self.convocatoria())
        # La otra versión (observación 2) no es la operativa: se rechaza al confirmar (clave diferida).
        self.rechaza('call_up', id=2, **self.convocatoria(lineup_observation_id=2, entry_index=1))
        self.rechaza('call_up', id=2, **self.convocatoria(lineup_key='event', lineup_observation_id=4))
        self.rechaza('call_up', id=2, **self.convocatoria(match_id=2, lineup_observation_id=3))

    def test_duplicados_legados_representables_y_entrada_unica(self):
        self.acepta('call_up', id=1, **self.convocatoria())
        # El mismo jugador dos veces en el mismo partido: lo rechaza la importación, no el esquema.
        self.acepta('call_up', id=2, **self.convocatoria(entry_index=1))
        self.rechaza('call_up', id=3, **self.convocatoria(external_player_id='1'))
        self.rechaza('call_up', id=3, **self.convocatoria(entry_index=2, side='neutral'))
        self.rechaza('call_up', id=3, **self.convocatoria(entry_index=2, played=2))
        self.rechaza('call_up', id=3, **self.convocatoria(entry_index=2, statistics_kind='vacio'))
        self.acepta('call_up', id=3, **self.convocatoria(entry_index=2, statistics_kind='absent', substitute='null',
                                                         shirt_number='"19"', played=None))
        self.rechaza('call_up', id=4, **self.convocatoria(entry_index=3, substitute='sí'))

    def test_estadisticas_originales_con_su_tipo_y_su_posicion(self):
        self.acepta('call_up', id=1, **self.convocatoria())
        valores = {'totalShots': 'true', 'goals': '1', 'saves': 'null', 'fouls': '"4"', 'blockedScoringAttempt': '-1',
                   'shotOffTarget': '1.5'}
        for orden, (clave, literal) in enumerate(valores.items()):
            self.acepta('call_up_stat', call_up_id=1, key=clave, ordinal=orden, value_json=literal)
        with self.motor.connect() as c:
            guardados = c.exec_driver_sql('SELECT key, value_json FROM call_up_stat WHERE call_up_id = 1 ORDER BY ordinal').fetchall()
        self.assertEqual(dict(guardados), valores)
        self.assertEqual([k for k, _ in guardados], list(valores))
        # Ausente: no hay fila (distinto de 'null' y de '0').
        self.assertIsNone(self.valor("SELECT value_json FROM call_up_stat WHERE key = 'onTargetScoringAttempt'"))
        self.rechaza('call_up_stat', call_up_id=1, key='goals', ordinal=9, value_json='2')
        self.rechaza('call_up_stat', call_up_id=1, key='rating', ordinal=0, value_json='7')
        self.rechaza('call_up_stat', call_up_id=1, key='rating', ordinal=9, value_json='NaN')
        self.rechaza('call_up_stat', call_up_id=1, key='rating', ordinal=9, value_json=None)

    def test_estado_del_partido_desde_su_event_o_listing_seleccionado(self):
        partido = dict(match_id=1, source_key='event', source_observation_id=4, external_home_team_id='2841',
                       external_away_team_id='24368', home_team_id=1, away_team_id=2, home_score='true',
                       away_score=None, round='"8"', calendar_source='event', version_conflict=False, rule_set_id=1, run_id=1)
        self.rechaza('match_state', **dict(partido, source_key='lineups', source_observation_id=1))
        self.rechaza('match_state', **dict(partido, calendar_source='listing'))
        self.rechaza('match_state', **dict(partido, version_conflict=2))
        self.rechaza('match_state', **dict(partido, home_score='1-0'))
        self.acepta('match_state', **partido)
        self.assertEqual(self.valor('SELECT home_score FROM match_state'), 'true')
        self.acepta('match_version_issue', match_id=1, reason='identity_changed')

    def test_estadisticas_e_incidencias_de_la_seccion_seleccionada(self):
        item = dict(match_id=1, observation_id=5, period_index=0, group_index=0, item_index=0, period='ALL',
                    key='totalShotsOnGoal', home_value='2', away_value='true', rule_set_id=1, run_id=1)
        self.acepta('team_match_stat', **item)
        # Un ítem sin clave ni valores también es evidencia de la sección.
        self.acepta('team_match_stat', **dict(item, item_index=1, key=None, home_value=None, away_value=None))
        self.rechaza('team_match_stat', **dict(item, observation_id=4, item_index=2))
        self.rechaza('incident', match_id=1, observation_id=5, seq=0, rule_set_id=1, run_id=1)

    def test_una_reconstruccion_cambia_seleccion_y_derivados_en_una_transaccion(self):
        self.acepta('call_up', id=1, **self.convocatoria())
        self.acepta('observation', id=6, source_file_id=1, line=6, kind='section', provider='sofascore',
                    external_event_id='100', key='lineups', fetched_at_valid=False, content_hash=H2, match_id=1)
        with self.motor.begin() as c:
            c.exec_driver_sql('UPDATE section_selection SET observation_id = 6 WHERE match_id = 1 AND "key" = \'lineups\'')
            c.exec_driver_sql('UPDATE call_up SET lineup_observation_id = 6 WHERE id = 1')
        # Cambiar la selección sin rehacer sus derivados no se confirma.
        with self.assertRaises(IntegrityError):
            with self.motor.begin() as c:
                c.exec_driver_sql('UPDATE section_selection SET observation_id = 1 WHERE match_id = 1 AND "key" = \'lineups\'')
        self.assertEqual(self.valor('SELECT lineup_observation_id FROM call_up WHERE id = 1'), 6)

    def test_discrepancias_y_atributos(self):
        self.acepta('discrepancy', id=1, kind='section', match_id=1, key='lineups', integrity=False,
                    selected_observation_id=1, changed_fields='["status"]', rule_set_id=1, run_id=1)
        self.rechaza('discrepancy', id=2, kind='section', player_id=1, key='lineups', integrity=False, rule_set_id=1, run_id=1)
        self.rechaza('discrepancy', id=2, kind='player_attribute', match_id=1, key='player', integrity=False,
                     rule_set_id=1, run_id=1)
        self.acepta('discrepancy', id=2, kind='player_attribute', player_id=1, key='player', integrity=False,
                    rule_set_id=1, run_id=1)
        self.acepta('discrepancy_value', discrepancy_id=2, field='country', ordinal=0, value_json='"Spain"')
        self.acepta('discrepancy_value', discrepancy_id=2, field='country', ordinal=1, value_json='"France"')
        self.acepta('discrepancy_reason', discrepancy_id=1, reason='comparable_versions_differ')
        self.acepta('discrepancy_observation', discrepancy_id=1, observation_id=2)
        atributo = dict(observation_id=1, entry_index=0, external_player_id='966303', player_id=1, value_json='165')
        self.acepta('player_attribute_observation', field='height_cm', **atributo)
        self.rechaza('player_attribute_observation', field='team', **atributo)
        self.acepta('team_name', team_id=1, name='Real Jaén', source_observation_id=4, rule_set_id=1, run_id=1)


class DecisionesLocales(Base):
    def test_sustituciones_auditadas(self):
        decision = dict(replaced_match_id=1, current_match_id=2, external_replaced_id='100', external_current_id='101',
                        reason='aplazado y jugado con otro id', decided_by='analista', decided_on='2026-10-22',
                        created_in_run=1, status='active')
        self.rechaza('local_replacement', id=1, **dict(decision, current_match_id=1))
        self.rechaza('local_replacement', id=1, **dict(decision, reason=' '))
        self.acepta('local_replacement', id=1, **decision)
        self.rechaza('local_replacement', id=2, **decision)
        self.rechaza('local_replacement', id=2, **dict(decision, status='reverted'))
        with self.motor.begin() as c:
            c.exec_driver_sql("UPDATE local_replacement SET status = 'reverted', reverted_in_run = 1, reverted_by = 'analista', "
                              "reverted_on = '2026-10-24', revert_reason = 'error' WHERE id = 1")
        self.acepta('local_replacement', id=2, **decision)
        self.assertEqual(self.valor('SELECT count(*) FROM local_replacement'), 2)


class SinDependenciasSQL(unittest.TestCase):
    def test_la_api_y_el_importador_no_cargan_la_persistencia(self):
        # Con este mismo intérprete (tenga o no SQLAlchemy): importarlos no carga sqlalchemy, alembic ni persistencia.
        codigo = ('import sys; sys.path.insert(0, r"%s"); import api, importar; '
                  'print(sorted(m for m in sys.modules if m.split(".")[0] in ("sqlalchemy", "alembic", "persistencia")))' % RAIZ)
        salida = subprocess.run([sys.executable, '-B', '-c', codigo], capture_output=True, text=True, check=True)
        self.assertEqual(salida.stdout.strip(), '[]')


if __name__ == '__main__':
    unittest.main()
