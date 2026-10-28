"""Durable handoff key: SQL fixtures only, no network or real data."""
import unittest
import uuid
import test_actualizacion as fixtures

@unittest.skipUnless(fixtures.CON_SQL,'SQL dependencies required')
class SolicitudDurable(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.Actualizacion()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_clave_durable_y_colision(self):
        from persistencia.actualizacion import Coordinador,CoordinacionRechazada
        ident=str(uuid.uuid4())
        with self.fixture.abrir() as c:
            first=c.solicitar(exportaciones=[(self.fixture.fixture,2841)],identificador=ident)
            self.assertEqual(c.esperar(first)['estado'],'completed')
            generation=c.capturar().id
            self.assertEqual(c.solicitar(exportaciones=[(self.fixture.fixture,2841)],identificador=ident),first)
            self.assertEqual(c.capturar().id,generation)
            with self.assertRaises(CoordinacionRechazada):c.solicitar(identificador=ident)
        with Coordinador(self.fixture.base,fixtures.AMBITO,reserva=0) as c:
            self.assertEqual(c.solicitar(exportaciones=[(self.fixture.fixture,2841)],identificador=ident),first)
            self.assertEqual(c.capturar().id,generation)


class Productores(unittest.TestCase):
    """Only reviewed producer fingerprints pass the trust boundary; the current producer is one of them."""
    def test_productor_actual_y_del_bundle_real_aprobados(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from acquisition.recipe import producer_rules
        from persistencia.adquisicion import productores_aprobados
        self.assertIn(producer_rules(), productores_aprobados())
        self.assertIn('e18eba33cfbfd11b6ac39c6046ddacfe4436bb8f0b40775ba6b770f8a8c4d65a', productores_aprobados())

    def test_productor_no_aprobado_rechazado_antes_de_verificar(self):
        from persistencia.adquisicion import verificar_sellado
        from persistencia.bundle import Rejected, encode
        body = encode(dict(manifest=dict(rules_sha256='0' * 64), files={}))
        with self.assertRaisesRegex(Rejected, 'Unapproved producer'):
            verificar_sellado(body, dict(bytes=len(body), sha256='0' * 64), {}, ('a', 'b'))
        with self.assertRaisesRegex(Rejected, 'Malformed'):
            verificar_sellado(b'{"manifest":{},"manifest":{}}', {}, {}, ('a', 'b'))

    def test_aprobacion_limitada_a_su_generacion(self):
        from persistencia.adquisicion import productores_aprobados, verificar_sellado
        from persistencia.bundle import Rejected, encode
        legacy = 'e18eba33cfbfd11b6ac39c6046ddacfe4436bb8f0b40775ba6b770f8a8c4d65a'
        self.assertEqual(productores_aprobados()[legacy], '0e80fdde-95d9-44b0-a2ea-8426bce14b03')
        body = encode(dict(manifest=dict(rules_sha256=legacy), files={}))
        with self.assertRaisesRegex(Rejected, 'another generation'):
            verificar_sellado(body, dict(bytes=len(body), sha256='0' * 64), dict(generation_id='otra'), ('a', 'b'))

    def test_aprobar_productores_no_cambia_la_huella_de_reglas(self):
        from persistencia import carga
        archivos = carga.archivos_de_codigo()
        self.assertNotIn('productores-adquisicion.json', archivos)
        self.assertFalse(any('productores' in a for a in archivos))
