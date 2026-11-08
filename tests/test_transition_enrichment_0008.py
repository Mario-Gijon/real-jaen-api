"""Focused, disposable-SQLite tests for preparing (not installing) Alembic 0008 candidates."""

import hashlib
import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest import mock

from alembic import command

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from scripts import transition_enrichment_0008 as transition  # noqa: E402
from persistencia import sqlite  # noqa: E402


class TransitionEnrichment0008Tests(unittest.TestCase):
    def setUp(self):
        from test_enrichment_persistence import EnrichmentPersistenceTests, alembic_config

        self.workspace = Path(tempfile.mkdtemp(prefix="rj-transition-0008-tests-"))
        self.addCleanup(shutil.rmtree, self.workspace, True)
        self.fixture = EnrichmentPersistenceTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.alembic_config = alembic_config
        self.source = self.fixture.database
        self.fixture.connection.close()
        command.downgrade(self.alembic_config(self.source), "0007")
        self.assertEqual(sqlite.revision(self.source), "0007")
        self.output = self.workspace / "candidate-output"
        self.updates = Path(str(self.source) + ".actualizaciones")

    def _prepare(self):
        return transition.prepare_candidate(self.source, self.output)

    def _assert_source_unchanged(self, original_description, original_file_hash):
        self.assertEqual(sqlite.describir(self.source), original_description)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), original_file_hash)

    def test_prepare_migra_enriquece_preserva_y_emite_informe_privado(self):
        original_description = sqlite.describir(self.source)
        original_file_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

        report = self._prepare()

        candidate = self.output / transition.CANDIDATE_NAME
        report_path = self.output / transition.REPORT_NAME
        self.assertEqual(report["status"], "candidate_not_installed")
        self.assertFalse(report["candidate_installed"])
        self.assertEqual(report["source_revision"], "0007")
        self.assertEqual(report["target_revision"], "0008")
        self.assertEqual(sqlite.revision(candidate), "0008")
        self.assertEqual(report["enrichment"]["selected_spanish_captures"], 2)
        self.assertEqual(report["enrichment"]["selected_empty_captures"], 1)
        self.assertEqual(report["enrichment"]["selected_available_comments"], 3)
        self.assertEqual(report["enrichment"]["first_run"]["inserted"], 3)
        self.assertEqual(report["enrichment"]["idempotency_run"]["inserted"], 0)
        self.assertEqual(report["enrichment"]["idempotency_run"]["unchanged"], 3)
        self.assertEqual(report["enrichment"]["selected_annotations"], 3)
        self.assertTrue(report["enrichment"]["coverage_complete"])
        self.assertTrue(report["preexisting_tables_preserved"])
        self.assertEqual(report["candidate_semantic_description"]["integridad"], "ok")
        self.assertEqual(report["candidate_semantic_description"]["claves_foraneas"], [])
        self.assertEqual(report["source_snapshot_semantic_sha256"], original_description["huella"])
        self.assertEqual(report["candidate_sqlite_sha256"], hashlib.sha256(candidate.read_bytes()).hexdigest())
        self.assertEqual(json.loads(report_path.read_text(encoding="utf-8")), report)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(candidate.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(report_path.stat().st_mode), 0o600)
        self.assertFalse(any(Path(str(candidate) + suffix).exists() for suffix in sqlite.ACOMPANANTES))
        self.assertFalse(self.updates.exists())
        self._assert_source_unchanged(original_description, original_file_hash)

    def test_rechaza_origen_invalido_revision_incorrecta_y_salida_existente(self):
        invalid_source = self.workspace / "invalid" / "not-sqlite.db"
        invalid_source.parent.mkdir()
        invalid_source.write_bytes(b"not a sqlite database")
        invalid_output = self.workspace / "invalid-output"
        with self.assertRaises(transition.PreparationRejected):
            transition.prepare_candidate(invalid_source, invalid_output)
        self.assertFalse(invalid_output.exists())

        wrong_source_dir = self.workspace / "wrong-source"
        wrong_source_dir.mkdir()
        wrong_revision = wrong_source_dir / "wrong-revision.sqlite"
        sqlite.copia_consistente(self.source, wrong_revision)
        command.upgrade(self.alembic_config(wrong_revision), "0008")
        wrong_revision_output = self.workspace / "wrong-revision-output"
        with self.assertRaisesRegex(transition.PreparationRejected, "0007"):
            transition.prepare_candidate(wrong_revision, wrong_revision_output)
        self.assertFalse(wrong_revision_output.exists())

        self.output.mkdir()
        with self.assertRaisesRegex(transition.PreparationRejected, "ya existe"):
            self._prepare()

    def test_rechaza_salida_dentro_del_arbol_de_origen_o_del_repositorio(self):
        inside_source = self.source.parent / "nested-candidate"
        with self.assertRaisesRegex(transition.PreparationRejected, "árbol"):
            transition.prepare_candidate(self.source, inside_source)

        inside_repository = ROOT / "not-a-real-candidate-output-for-test"
        with self.assertRaisesRegex(transition.PreparationRejected, "repositorio"):
            transition.prepare_candidate(self.source, inside_repository)
        self.assertFalse(inside_repository.exists())

    def test_fallo_en_migracion_o_enrichment_limpia_candidata_y_no_toca_origen(self):
        original_description = sqlite.describir(self.source)
        original_file_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        for phase in ("migration", "enrichment"):
            with self.subTest(phase=phase):
                output = self.workspace / f"failed-{phase}"
                if phase == "migration":
                    patcher = mock.patch.object(transition.command, "upgrade", side_effect=RuntimeError("injected"))
                else:
                    patcher = mock.patch.object(
                        transition, "persist_candidate_enrichments", side_effect=RuntimeError("injected"),
                    )
                with patcher, self.assertRaisesRegex(RuntimeError, "injected"):
                    transition.prepare_candidate(self.source, output)
                self.assertFalse(output.exists())
                self._assert_source_unchanged(original_description, original_file_hash)

    def test_rechaza_paquete_con_manifest_y_journal_sin_tocarlos(self):
        package = self.workspace / "coordinator-backup"
        payload = package / "payload"
        payload.mkdir(parents=True)
        packaged_source = payload / self.source.name
        sqlite.copia_consistente(self.source, packaged_source)
        manifest = package / "manifest.json"
        manifest.write_text('{"complete": true}', encoding="utf-8")
        journal_dir = Path(str(packaged_source) + ".actualizaciones")
        journal_dir.mkdir()
        journal = journal_dir / "jobs.sqlite"
        with closing(sqlite3.connect(journal)) as connection:
            connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            connection.execute("INSERT INTO marker VALUES ('coordinator-history')")
            connection.commit()
        protected_hashes = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (packaged_source, manifest, journal)
        }
        output = self.workspace / "refused-package-output"

        with self.assertRaisesRegex(transition.PreparationRejected, "journal o manifiesto"):
            transition.prepare_candidate(packaged_source, output)

        self.assertFalse(output.exists())
        for path, digest in protected_hashes.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
