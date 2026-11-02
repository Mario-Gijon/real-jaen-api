"""Update control (control.py) with the real coordinator on SQL fixtures and a simulated acquisition client."""
import json
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path

import test_actualizacion as fixtures

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeCliente:
    """What persistencia.adquisicion.Cliente.publicar does, seen from the control: phases, records, coordinator."""
    def __init__(self, fixture):
        self.fixture, self.mode, self.calls, self.cancelled = fixture, 'ok', [], []
        self.coverage = None
        self.gate = threading.Event()
        self.gate.set()

    def cancelar(self, acquisition):
        self.cancelled.append(acquisition)
        if self.mode != 'ignore-cancel':
            self.gate.set()

    def publicar(self, coord, request_id, recipe, avance):
        from persistencia.adquisicion import AdquisicionRechazada
        from persistencia.bundle import Rejected
        self.calls.append(request_id)
        acquisition = str(uuid.uuid5(uuid.NAMESPACE_URL, request_id))
        avance('acquiring', acquisition_id=acquisition, acquisition_state='running')
        self.gate.wait(10)
        if acquisition in self.cancelled and self.mode != 'ignore-cancel':
            raise AdquisicionRechazada('Adquisicion cancelled: no se importa nada (None)')
        if self.mode == 'refused':
            raise AdquisicionRechazada('Adquisicion failed: no se importa nada (provider_failed)')
        avance('verifying')
        if self.mode == 'tampered':
            raise Rejected('Incomplete sporting coverage')
        avance('publishing')
        folder = coord.directorio / 'adquisiciones' / request_id
        folder.mkdir(parents=True, exist_ok=True)
        record = folder / 'solicitud.json'
        intentos = json.loads(record.read_bytes())['intentos'] if record.exists() else []
        ident = str(uuid.uuid5(uuid.NAMESPACE_URL, f'{request_id}:{len(intentos) + 1}'))
        record.write_text(json.dumps(dict(intentos=intentos + [ident])))
        coord.solicitar(exportaciones=[(self.fixture.fixture, 2841)], identificador=ident)
        result = coord.esperar(ident)
        if result['estado'] != 'completed':
            raise AdquisicionRechazada('Publicacion fallida: se conserva la generacion valida')
        return dict(generation_id=result['generacion'], coverage=self.coverage)


def wait(control, ident, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = control.get(ident)
        if item['status'] not in ('queued', 'running'):
            return item
        time.sleep(.02)
    raise TimeoutError(ident)


def phase(control, ident, value, timeout=10):
    deadline = time.monotonic() + timeout
    while control.get(ident)['phase'] != value:
        if time.monotonic() > deadline:
            raise TimeoutError(value)
        time.sleep(.01)


@unittest.skipUnless(fixtures.CON_SQL, 'SQL dependencies required')
class Control(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.Actualizacion()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.cliente = FakeCliente(self.fixture)

    def open(self, **kwargs):
        from control import Actualizaciones
        coord = self.fixture.abrir(**kwargs)
        control = Actualizaciones(coord, self.cliente)
        self.addCleanup(lambda: (self.cliente.gate.set(), control.close()))
        return coord, control

    def test_publica_y_misma_clave_devuelve_el_mismo_trabajo(self):
        coord, control = self.open()
        with coord:
            before = coord.capturar().id
            key = str(uuid.uuid4())
            job, reused = control.solicitar(key)
            self.assertFalse(reused)
            done = wait(control, job['id'])
            self.assertEqual((done['status'], done['published']), ('completed', True))
            self.assertNotEqual(coord.capturar().id, before)
            again, reused = control.solicitar(key)
            self.assertEqual((again['id'], reused), (job['id'], True))
            current = control.actual()
            self.assertEqual(current['last_published']['at'], done['result']['published_at'])
            self.assertEqual((current['scope']['kind'], current['scope']['action']), ('group', 'Actualizar Grupo 2'))
            self.assertEqual(len(self.cliente.calls), 1)

    def test_un_unico_trabajo_activo(self):
        self.cliente.gate.clear()
        coord, control = self.open()
        with coord:
            first, _ = control.solicitar(str(uuid.uuid4()))
            second, reused = control.solicitar(str(uuid.uuid4()))
            self.assertEqual((second['id'], reused), (first['id'], True))
            self.cliente.gate.set()
            wait(control, first['id'])
            self.assertEqual(len(self.cliente.calls), 1)

    def test_cancelar_al_adquirir_no_importa_y_es_idempotente(self):
        from control import NotCancellable
        self.cliente.gate.clear()
        coord, control = self.open()
        with coord:
            before = coord.capturar().id
            job, _ = control.solicitar(str(uuid.uuid4()))
            phase(control, job['id'], 'acquiring')
            self.assertTrue(control.public(control.get(job['id']))['cancellable'])
            control.cancelar(job['id'])
            done = wait(control, job['id'])
            self.assertEqual((done['status'], done['error']['code'], done['published']), ('cancelled', 'CANCELLED', False))
            self.assertEqual(control.cancelar(job['id'])['status'], 'cancelled')
            self.assertEqual(coord.capturar().id, before)
            finished, _ = control.solicitar(str(uuid.uuid4()))
            wait(control, finished['id'])
            with self.assertRaises(NotCancellable):
                control.cancelar(finished['id'])

    def test_cancelacion_aceptada_antes_de_verificar_impide_publicar(self):
        self.cliente.mode = 'ignore-cancel'  # the acquisition finished anyway
        self.cliente.gate.clear()
        coord, control = self.open()
        with coord:
            before = coord.capturar().id
            job, _ = control.solicitar(str(uuid.uuid4()))
            phase(control, job['id'], 'acquiring')
            control.cancelar(job['id'])
            self.cliente.gate.set()
            done = wait(control, job['id'])
            self.assertEqual(done['status'], 'cancelled')
            self.assertEqual(coord.capturar().id, before)

    def test_rechazo_del_proveedor_conserva_la_generacion_y_no_reutiliza(self):
        self.cliente.mode = 'refused'
        coord, control = self.open()
        with coord:
            before = coord.capturar().id
            job, _ = control.solicitar(str(uuid.uuid4()))
            done = wait(control, job['id'])
            self.assertEqual((done['status'], done['error']['code']), ('failed', 'ACQUISITION_FAILED'))
            self.assertEqual(coord.capturar().id, before)
            self.cliente.mode = 'ok'
            again, _ = control.solicitar(str(uuid.uuid4()))
            self.assertNotEqual(again['request_id'], job['request_id'])
            self.assertFalse(wait(control, again['id'])['result']['reused_bundle'])

    def test_los_aplazados_de_la_ultima_publicacion_siguen_visibles_tras_un_rechazo(self):
        # A group publication with deferred refreshes, then a refused update: the status keeps that coverage, with
        # the names, while the previous data are served.
        self.cliente.coverage = dict(new=3, kept=40, pending=[], new_matches=[], rounds=dict(near=[9], far=[], failed=[]),
                                     deferred=[999])
        coord, control = self.open()
        with coord:
            done = wait(control, control.solicitar(str(uuid.uuid4()))[0]['id'])
            self.cliente.mode = 'refused'
            failed = wait(control, control.solicitar(str(uuid.uuid4()))[0]['id'])
            self.assertEqual(failed['status'], 'failed')
            current = control.actual()
            self.assertEqual(current['last_job']['id'], failed['id'])
            self.assertEqual(current['last_published']['at'], done['result']['published_at'])
            self.assertEqual(current['last_published']['coverage']['deferred'], [999])
            self.assertIn('999', current['last_published']['coverage']['names'])

    def test_bundle_rechazado(self):
        self.cliente.mode = 'tampered'
        coord, control = self.open()
        with coord:
            before = coord.capturar().id
            job, _ = control.solicitar(str(uuid.uuid4()))
            done = wait(control, job['id'])
            self.assertEqual((done['error']['code'], done['error']['retryable']), ('BUNDLE_REJECTED', False))
            self.assertEqual(coord.capturar().id, before)

    def test_fallo_al_publicar_y_reintento_con_la_misma_solicitud(self):
        failures = []
        def fault(point):
            if point == 'candidate_committed' and not failures:
                failures.append(point)
                raise RuntimeError('Injected failure after candidate commit')
        coord, control = self.open(_frontera=fault)
        with coord:
            before = coord.capturar().id
            job, _ = control.solicitar(str(uuid.uuid4()))
            done = wait(control, job['id'])
            self.assertEqual((done['error']['code'], done['published']), ('PUBLICATION_FAILED', False))
            self.assertEqual(coord.capturar().id, before)  # the last valid generation is still served
            retry, _ = control.solicitar(str(uuid.uuid4()))
            self.assertEqual(retry['request_id'], job['request_id'])
            finished = wait(control, retry['id'])
            self.assertEqual((finished['status'], finished['result']['reused_bundle']), ('completed', True))
            self.assertNotEqual(coord.capturar().id, before)

    def test_reinicio_concilia_sin_reanudar(self):
        from control import Actualizaciones
        coord, control = self.open()
        with coord:
            done, _ = control.solicitar(str(uuid.uuid4()))
            done = wait(control, done['id'])
            # Crash image while acquiring: the durable row is still active; a new control over the same coordinator.
            self.cliente.gate.clear()
            pending, _ = control.solicitar(str(uuid.uuid4()))
            phase(control, pending['id'], 'acquiring')
            restarted = Actualizaciones(coord, self.cliente)
            self.assertEqual(restarted.get(pending['id'])['error']['code'], 'INTERRUPTED')
            self.assertIsNone(restarted.actual()['active_job'])
            self.cliente.mode = 'refused'  # the orphaned worker of the old process must not publish either
            self.cliente.gate.set()
            time.sleep(.3)
            # Crash image after the coordinator confirmed: active row, confirmed attempt in the coordinator's journal.
            control.save(dict(done, status='running', phase='publishing'))
            again = Actualizaciones(coord, self.cliente)
            self.assertEqual((again.get(done['id'])['status'], again.get(done['id'])['published']), ('completed', True))
            self.assertEqual(len(self.cliente.calls), 2)  # nothing resumed



if __name__ == '__main__':
    unittest.main()
