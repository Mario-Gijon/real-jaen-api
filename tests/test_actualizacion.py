"""Actualizacion interna: solo fixtures y bases temporales, nunca red ni servicios reales."""
import concurrent.futures
from contextlib import closing
import errno
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_carga import Base, AMBITO, CON_SQL, respuestas
from test_importar import JAEN, RIVAL, evento, linea

if CON_SQL:
    from persistencia import actualizacion, carga, lectura, sincronizacion, sqlite
    from persistencia.actualizacion import Coordinador, CoordinacionRechazada
    from persistencia.decisiones import cargar_sustituciones


@unittest.skipUnless(CON_SQL, 'requiere dependencias SQL')
class Actualizacion(Base):
    def setUp(self):
        super().setUp()
        carga.cargar(self.importacion(), self.base)
        cargar_sustituciones(self.base, self.sustituciones((300, 301)), AMBITO)
        self.fixture = self.tmp / 'nuevo.jsonl'
        self.fixture.write_text(json.dumps(linea(999, 'event', '2026-10-28T12:00:00+00:00',
            evento(999, JAEN, RIVAL, marcador=(4, 1)))) + '\n', encoding='utf-8')
        self.antes = sqlite.describir(self.base)

    def abrir(self, **kwargs):
        coordinador = Coordinador(self.base, AMBITO, reserva=0, **kwargs)
        self.addCleanup(coordinador.close)
        return coordinador

    def pedir(self, c):
        return c.solicitar(exportaciones=[(self.fixture, JAEN)])

    def test_exito_lectores_ids_originales_decisiones_y_respuestas(self):
        ids = {t: self.consulta(f'SELECT * FROM {t} ORDER BY 1') for t in
               ('team', 'player', 'match', 'observation', 'content', 'local_replacement')}
        pausado, seguir = threading.Event(), threading.Event()
        def frontera(punto):
            if punto == 'principal_replaced':
                pausado.set()
                assert seguir.wait(10)
        c = self.abrir(_frontera=frontera)
        vieja = c.capturar()
        previas = respuestas(vieja._datos)
        ident = self.pedir(c)
        self.assertTrue(pausado.wait(10))
        self.assertIs(c.capturar(), vieja)
        self.assertEqual(respuestas(vieja._datos), previas)
        seguir.set()
        self.assertEqual(c.esperar(ident)['estado'], 'completed')
        nueva = c.capturar()
        self.assertNotEqual(nueva.id, vieja.id)
        self.assertEqual(vieja.consultar('/api/v1/matches/999')[0], 404)
        self.assertEqual(nueva.consultar('/api/v1/matches/999')[0], 200)
        self.assertEqual(respuestas(vieja._datos), previas)
        c.close()
        self.assertEqual(respuestas(nueva._datos), respuestas(lectura.cargar_desde_base(self.base, AMBITO)))
        for tabla, filas in ids.items():
            actuales = self.consulta(f'SELECT * FROM {tabla} ORDER BY 1')
            self.assertTrue(set(filas) <= set(actuales), tabla)
        # Respuesta ajena no puede mutar la instantanea publicada.
        _, cuerpo = nueva.consultar('/api/v1/matches/999')
        cuerpo.clear()
        self.assertTrue(nueva.consultar('/api/v1/matches/999')[1])
        c.close()
        with Coordinador(self.base, AMBITO, reserva=0) as reabierto:
            self.assertEqual(reabierto.capturar().id, nueva.id)
            self.assertEqual(reabierto.estado(ident)['estado'], 'completed')

    def test_sin_cambios_y_rechazo_de_otra_instancia_o_cli(self):
        with self.abrir() as c:
            anteriores = respuestas(c.capturar()._datos)
            ident = c.solicitar()
            estado = c.esperar(ident)
            self.assertEqual(estado['estado'], 'completed')
            self.assertEqual(estado['resultado']['estado'], 'no_changes')
            self.assertEqual(respuestas(c.capturar()._datos), anteriores)
            with self.assertRaises(Exception):
                Coordinador(self.base, AMBITO)
            p = subprocess.run([sys.executable, '-B', '-m', 'persistencia', 'migrar', '--base', str(self.base)],
                               capture_output=True, text=True, timeout=15)
            self.assertEqual(p.returncode, 1)
            self.assertIn('Base en uso', p.stderr)

    def test_concurrentes_deduplicadas(self):
        espera, continuar = threading.Event(), threading.Event()
        def pausa(p):
            if p == 'before_backup':
                espera.set()
                assert continuar.wait(10)
        with self.abrir(_frontera=pausa) as c:
            primero = self.pedir(c)
            self.assertTrue(espera.wait(5))
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                ids = list(pool.map(lambda _: self.pedir(c), range(24)))
            self.assertEqual(set(ids), {primero})
            continuar.set()
            self.assertEqual(c.esperar(primero)['estado'], 'completed')
            with closing(sqlite.sqlite3.connect(c.journal)) as j:
                self.assertEqual(j.execute('SELECT count(*) FROM trabajo').fetchone()[0], 1)

    def test_fallos_antes_y_despues_commit_y_recuperacion(self):
        for punto in ('before_backup', 'backup_saved', 'before_import', 'candidate_committed',
                      'snapshot_prepared', 'publication_prepared', 'principal_replaced', 'before_confirmation'):
            with self.subTest(punto=punto):
                visitadas = []
                def fallo(p):
                    visitadas.append(p)
                    if p == punto:
                        raise RuntimeError('fallo inyectado ' + p)
                with self.abrir(_frontera=fallo) as c:
                    vieja = c.capturar()
                    estado = c.esperar(self.pedir(c))
                    self.assertEqual(estado['estado'], 'failed', estado)
                    self.assertIn(punto, visitadas)
                    self.assertIs(c.capturar(), vieja)
                    self.assertEqual(sqlite.describir(self.base), self.antes)

    def test_error_post_confirmacion_no_revierte_y_commit_ambiguo(self):
        for punto in ('confirmed', 'swapped'):
            with self.subTest(punto=punto):
                def fallo(p):
                    if p == punto:
                        raise OSError('fallo despues del COMMIT')
                with self.abrir(_frontera=fallo) as c:
                    ident = self.pedir(c)
                    self.assertEqual(c.esperar(ident)['estado'], 'completed')
                    self.assertEqual(c.capturar().consultar('/api/v1/matches/999')[0], 200)
        with self.abrir() as c:
            guardar = c._guardar
            def ambiguo(trabajo, meta=None):
                guardar(trabajo, meta)
                if meta:
                    raise OSError('COMMIT confirmado pero excepcion al devolver')
            with mock.patch.object(c, '_guardar', side_effect=ambiguo):
                ident = c.solicitar()
                self.assertEqual(c.esperar(ident)['estado'], 'completed')
                self.assertEqual(c.capturar().id, c.estado(ident)['generacion'])

    def test_fallo_transaccional_en_importador_existente(self):
        original = sincronizacion.exigir_coherencia
        def fallar(c):
            if c.exec_driver_sql("SELECT count(*) FROM match_external_id WHERE external_id='999'").scalar():
                raise RuntimeError('fallo antes del COMMIT real')
            return original(c)
        with self.abrir() as coordinador, mock.patch.object(sincronizacion, 'exigir_coherencia', side_effect=fallar):
            estado = coordinador.esperar(self.pedir(coordinador))
            self.assertEqual(estado['estado'], 'failed')
            candidata = Path(estado['candidata'])
            with closing(sqlite._lectura(candidata)) as c:
                self.assertEqual(c.execute('SELECT count(*) FROM match_external_id WHERE external_id=999').fetchone()[0], 0)
            self.assertEqual(sqlite.describir(self.base), self.antes)

    def test_recuperacion_fallida_bloquea_y_se_reintenta_al_abrir(self):
        def fallo(p):
            if p == 'principal_replaced':
                raise RuntimeError('no publicar')
        c = self.abrir(_frontera=fallo)
        restaurar = c._restaurar
        def no_recuperar(copia, descripcion):
            if str(copia).endswith('candidata.sqlite'):
                return restaurar(copia, descripcion)
            raise OSError(errno.ENOSPC, 'disco lleno al recuperar')
        with mock.patch.object(c, '_restaurar', side_effect=no_recuperar):
            ident = self.pedir(c)
            with self.assertRaises(CoordinacionRechazada):
                c.esperar(ident)
            with self.assertRaises(CoordinacionRechazada):
                c.solicitar()
        c.close()
        with Coordinador(self.base, AMBITO, reserva=0) as reabierto:
            self.assertEqual(sqlite.describir(self.base), self.antes)
            self.assertEqual(reabierto.estado(ident)['estado'], 'interrupted')
            self.assertEqual(reabierto.capturar().consultar('/api/v1/matches/999')[0], 404)

    def test_codigo_ambito_y_disco(self):
        c = self.abrir()
        with mock.patch.object(actualizacion, 'codigo', return_value='otra-huella'):
            estado = c.esperar(self.pedir(c))
            self.assertEqual(estado['estado'], 'failed')
            self.assertIn('codigo', estado['error'])
        with mock.patch.object(actualizacion.shutil, 'disk_usage') as uso:
            uso.return_value.free = 0
            self.assertIn('Espacio', c.esperar(self.pedir(c))['error'])
        c.close()
        with mock.patch.object(actualizacion, 'codigo', return_value='otra-huella'):
            with self.assertRaises(CoordinacionRechazada):
                Coordinador(self.base, AMBITO)
        self.assertEqual(sqlite.describir(self.base), self.antes)

    def test_enospc_copia_y_journal_antes_de_confirmar(self):
        original = sqlite.copia_consistente
        def sin_espacio(origen, destino):
            if Path(destino).name == 'candidata.sqlite':
                raise OSError(errno.ENOSPC, 'sin espacio para candidata')
            return original(origen, destino)
        with self.abrir() as c, mock.patch.object(sqlite, 'copia_consistente', side_effect=sin_espacio):
            estado = c.esperar(self.pedir(c))
            self.assertEqual(estado['estado'], 'failed')
            self.assertEqual(sqlite.describir(self.base), self.antes)
        with self.abrir() as c:
            guardar = c._guardar
            def journal_lleno(trabajo, meta=None):
                if meta:
                    raise OSError(errno.ENOSPC, 'sin espacio para confirmar journal')
                guardar(trabajo, meta)
            with mock.patch.object(c, '_guardar', side_effect=journal_lleno):
                estado = c.esperar(self.pedir(c))
                self.assertEqual(estado['estado'], 'failed')
                self.assertEqual(sqlite.describir(self.base), self.antes)
                self.assertEqual(c.capturar().consultar('/api/v1/matches/999')[0], 404)

    def test_fallo_de_replace_usa_la_misma_restauracion(self):
        original = sqlite.os.replace
        llamadas = []
        def replace(origen, destino):
            llamadas.append(str(destino))
            if len(llamadas) == 1:
                raise OSError(errno.ENOSPC, 'fallo de replace')
            return original(origen, destino)
        with self.abrir() as c, mock.patch.object(sqlite.os, 'replace', side_effect=replace):
            estado = c.esperar(self.pedir(c))
            self.assertEqual(estado['estado'], 'failed')
            self.assertEqual(len(llamadas), 2)
            self.assertEqual(sqlite.describir(self.base), self.antes)

    def test_confirmacion_ilegible_bloquea_capturas_y_reconcilia(self):
        def fallo(p):
            if p == 'confirmed':
                raise OSError('confirmacion ambigua')
        c = self.abrir(_frontera=fallo)
        vieja = c.capturar()
        leer = c._leer
        def ilegible(ident):
            trabajo = leer(ident)
            if trabajo['estado'] == 'completed':
                raise OSError('journal temporalmente ilegible')
            return trabajo
        with mock.patch.object(c, '_leer', side_effect=ilegible):
            ident = self.pedir(c)
            limite = time.monotonic() + 10
            while c._bloqueado is None and time.monotonic() < limite:
                time.sleep(0.01)
            self.assertIsNotNone(c._bloqueado)
            with self.assertRaises(CoordinacionRechazada):
                c.capturar()
            with self.assertRaises(CoordinacionRechazada):
                c.solicitar()
            self.assertEqual(vieja.consultar('/api/v1/matches/999')[0], 404)
        c.close()
        with Coordinador(self.base, AMBITO, reserva=0) as nuevo:
            self.assertEqual(nuevo.estado(ident)['estado'], 'completed')
            self.assertEqual(nuevo.capturar().id, nuevo.estado(ident)['generacion'])
            self.assertEqual(nuevo.capturar().consultar('/api/v1/matches/999')[0], 200)

    def test_cambio_ambito_rechazado_sin_tocar_principal(self):
        otro = self.tmp / 'otro-ambito.json'
        otro.write_bytes(AMBITO.read_bytes() + b'\n')
        with self.abrir() as c:
            with mock.patch.object(c, 'ambito', otro):
                estado = c.esperar(self.pedir(c))
                self.assertEqual(estado['estado'], 'failed')
                self.assertIn('ambito', estado['error'])
            self.assertEqual(sqlite.describir(self.base), self.antes)
        with self.assertRaises(CoordinacionRechazada):
            Coordinador(self.base, otro)

    def test_terminacion_y_reinicio_en_fronteras(self):
        puntos = ('before_backup', 'backup_saved', 'before_import', 'candidate_committed',
                  'snapshot_prepared', 'publication_prepared', 'principal_replaced',
                  'before_confirmation', 'confirmed', 'swapped', 'recovered')
        for numero, punto in enumerate(puntos):
            with self.subTest(punto=punto):
                base = self.tmp / f'crash-{numero}.sqlite'
                sqlite.copia_consistente(self.base, base)
                code = '''import os, sys
from persistencia.actualizacion import Coordinador
punto = sys.argv[4]
def frontera(p):
    if punto == 'recovered' and p == 'principal_replaced':
        raise RuntimeError('recuperar')
    if p == punto:
        print(p, flush=True)
        os._exit(73)
c = Coordinador(sys.argv[1], sys.argv[2], reserva=0, _frontera=frontera)
i = c.solicitar(exportaciones=[(sys.argv[3], int(sys.argv[5]))])
try:
    resultado = c.esperar(i)
    raise RuntimeError('La frontera no se alcanzo: ' + str(resultado))
finally:
    c.close()
'''
                p = subprocess.run([sys.executable, '-B', '-u', '-c', code, str(base), str(AMBITO),
                                    str(self.fixture), punto, str(JAEN)], capture_output=True, text=True, timeout=30)
                self.assertEqual(p.returncode, 73, p.stderr)
                self.assertIn(punto, p.stdout)
                with Coordinador(base, AMBITO, reserva=0) as nuevo:
                    publicado = punto in ('confirmed', 'swapped')
                    self.assertEqual(nuevo.capturar().consultar('/api/v1/matches/999')[0], 200 if publicado else 404)
                    estados = nuevo._activos()
                    self.assertEqual(estados, [])
                    if not publicado:
                        self.assertEqual(sqlite.describir(base), self.antes)
                    # Reiniciar no importa el fixture ni crea otro trabajo.
                    with closing(sqlite.sqlite3.connect(nuevo.journal)) as j:
                        self.assertEqual(j.execute('SELECT count(*) FROM trabajo').fetchone()[0], 1)
