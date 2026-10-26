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
