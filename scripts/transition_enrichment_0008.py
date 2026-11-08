#!/usr/bin/env python3
"""Prepare an unpublished SQLite candidate for the 0007 -> 0008 enrichment transition.

This utility intentionally has no install, adopt, or restore operation. It accepts a standalone SQLite file only;
coordinator backup packages must be validated as a separate, coherent base+journal+manifest set before use.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alembic import command  # noqa: E402
from enrichment.store import (  # noqa: E402
    DEFAULT_ENRICHMENT_VERSION,
    persist_candidate_enrichments,
)
from persistencia import carga, sqlite  # noqa: E402
from persistencia.bloqueo import bloquear  # noqa: E402


SOURCE_REVISION = "0007"
TARGET_REVISION = "0008"
REPORT_NAME = "prepare-report.json"
CANDIDATE_NAME = "candidate.sqlite"
ENRICHMENT_TABLES = {
    "narrative_comment_enrichment": (
        "capture_id", "source_comment_id", "enrichment_version", "category", "attributes_json",
    ),
    "narrative_comment_enrichment_participant": (
        "capture_id", "source_comment_id", "enrichment_version", "participant_position", "source_role",
        "role", "source_id_json", "normalizer_role",
    ),
}
ENRICHMENT_INDEXES = {
    "ix_narrative_comment_enrichment_category_version": (
        "narrative_comment_enrichment", ("category", "enrichment_version"),
    ),
    "ix_narrative_comment_enrichment_version": (
        "narrative_comment_enrichment", ("enrichment_version",),
    ),
    "ix_narrative_comment_enrichment_participant_player_version": (
        "narrative_comment_enrichment_participant", ("source_id_json", "enrichment_version"),
    ),
    "ix_narrative_comment_enrichment_participant_role_version": (
        "narrative_comment_enrichment_participant", ("role", "enrichment_version"),
    ),
}


class PreparationRejected(RuntimeError):
    """The source or candidate could not be verified safely."""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)


def _description(path: Path) -> dict[str, Any]:
    try:
        result = sqlite.describir(path)
    except sqlite3.Error as error:
        raise PreparationRejected(f"No se pudo describir SQLite: {path}") from error
    if result["integridad"] != "ok":
        raise PreparationRejected(f"integrity_check no es correcto para {path}: {result['integridad']}")
    if result["claves_foraneas"]:
        raise PreparationRejected(f"foreign_key_check encontró errores en {path}: {result['claves_foraneas'][:5]}")
    return result


def _table_inventory(path: Path) -> dict[str, dict[str, Any]]:
    """Hash each table's ordered rows without changing the source or loading all rows into memory."""
    result: dict[str, dict[str, Any]] = {}
    with closing(_read_only(path)) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name <> 'alembic_version' ORDER BY name"
        )]
        for table in tables:
            quoted_table = table.replace('"', '""')
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{quoted_table}")')]
            if not columns:
                raise PreparationRejected(f"La tabla no tiene columnas legibles: {table}")
            projection = ", ".join(f'"{column.replace(chr(34), chr(34) * 2)}"' for column in columns)
            ordering = projection
            digest = hashlib.sha256()
            digest.update(_json_bytes([table, columns]))
            count = 0
            cursor = connection.execute(f'SELECT {projection} FROM "{quoted_table}" ORDER BY {ordering}')
            while rows := cursor.fetchmany(512):
                for row in rows:
                    digest.update(_json_bytes([repr(tuple(row))]))
                    count += 1
            result[table] = {"columns": columns, "rows": count, "sha256": digest.hexdigest()}
    return result


def _schema_objects(path: Path) -> dict[tuple[str, str], dict[str, str | None]]:
    with closing(_read_only(path)) as connection:
        rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    return {(kind, name): {"table": table, "sql": sql} for kind, name, table, sql in rows}


def _index_columns(connection: sqlite3.Connection, index: str) -> tuple[str, ...]:
    quoted = index.replace('"', '""')
    return tuple(row[2] for row in connection.execute(f'PRAGMA index_info("{quoted}")'))


def _foreign_keys(connection: sqlite3.Connection, table: str) -> set[tuple[str, tuple[tuple[str, str], ...]]]:
    quoted = table.replace('"', '""')
    groups: dict[int, list[tuple[int, str, str, str]]] = {}
    for ident, sequence, target, source_column, target_column, *_rest in connection.execute(
        f'PRAGMA foreign_key_list("{quoted}")'
    ):
        groups.setdefault(ident, []).append((sequence, target, source_column, target_column))
    return {
        (rows[0][1], tuple((source, target) for _sequence, _table, source, target in sorted(rows)))
        for rows in groups.values()
    }


def _verify_enrichment_schema(path: Path) -> None:
    expected_primary_keys = {
        "narrative_comment_enrichment": ("capture_id", "source_comment_id", "enrichment_version"),
        "narrative_comment_enrichment_participant": (
            "capture_id", "source_comment_id", "enrichment_version", "participant_position",
        ),
    }
    expected_unique_constraints = {
        "narrative_comment_enrichment": {
            ("capture_id", "source_comment_id", "enrichment_version"),
        },
        "narrative_comment_enrichment_participant": {
            ("capture_id", "source_comment_id", "enrichment_version", "participant_position"),
            ("capture_id", "source_comment_id", "enrichment_version", "source_role"),
        },
    }
    expected_checks = {
        "narrative_comment_enrichment": (
            "length(trim(enrichment_version)) > 0",
            "category is null or length(trim(category)) > 0",
            "json_valid(attributes_json) and json_type(attributes_json) = 'object'",
        ),
        "narrative_comment_enrichment_participant": (
            "participant_position >= 0",
            "length(trim(source_role)) > 0",
            "role is null or length(trim(role)) > 0",
            "normalizer_role is null or length(trim(normalizer_role)) > 0",
            "source_id_json is null or (json_valid(source_id_json) and json_type(source_id_json) in ('integer', 'text'))",
        ),
    }
    with closing(_read_only(path)) as connection:
        for table, expected_columns in ENRICHMENT_TABLES.items():
            info = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            if tuple(row[1] for row in info) != expected_columns:
                raise PreparationRejected(f"La tabla {table} no coincide con el esquema autorizado de 0008")
            primary_key = tuple(row[1] for row in sorted((row for row in info if row[5]), key=lambda row: row[5]))
            if primary_key != expected_primary_keys[table]:
                raise PreparationRejected(f"La clave primaria de {table} no coincide con 0008")

            unique_constraints = set()
            for index_row in connection.execute(f'PRAGMA index_list("{table}")'):
                if index_row[2]:
                    unique_constraints.add(_index_columns(connection, index_row[1]))
            if unique_constraints != expected_unique_constraints[table]:
                raise PreparationRejected(f"Las restricciones únicas de {table} no coinciden con 0008")

            definition = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,),
            ).fetchone()[0]
            normalized_definition = " ".join(definition.lower().split())
            checks = re.findall(r"\bcheck\s*\(", normalized_definition)
            if len(checks) != len(expected_checks[table]) or any(
                fragment not in normalized_definition for fragment in expected_checks[table]
            ):
                raise PreparationRejected(f"Las restricciones CHECK de {table} no coinciden con 0008")

        expected_indexes = {name for name in ENRICHMENT_INDEXES}
        actual_indexes: set[str] = set()
        for table in ENRICHMENT_TABLES:
            quoted = table.replace('"', '""')
            actual_indexes.update(row[1] for row in connection.execute(f'PRAGMA index_list("{quoted}")')
                                  if not row[1].startswith("sqlite_autoindex_"))
        if actual_indexes != expected_indexes:
            raise PreparationRejected(f"Índices enrichment inesperados: {sorted(actual_indexes)}")
        for index, (table, expected_columns) in ENRICHMENT_INDEXES.items():
            if _index_columns(connection, index) != expected_columns:
                raise PreparationRejected(f"El índice {index} no coincide con el esquema autorizado de 0008")

        expected_fks = {
            "narrative_comment_enrichment": {
                ("narrative_comment", (("capture_id", "capture_id"),
                                        ("source_comment_id", "source_comment_id"))),
            },
            "narrative_comment_enrichment_participant": {
                ("narrative_comment_enrichment", (
                    ("capture_id", "capture_id"),
                    ("source_comment_id", "source_comment_id"),
                    ("enrichment_version", "enrichment_version"),
                )),
            },
        }
        for table, expected in expected_fks.items():
            if _foreign_keys(connection, table) != expected:
                raise PreparationRejected(f"Las claves foráneas de {table} no coinciden con 0008")


def _verify_schema_transition(before: dict, after: dict) -> list[str]:
    before_keys, after_keys = set(before), set(after)
    if not before_keys.issubset(after_keys):
        raise PreparationRejected("La migración eliminó o renombró objetos SQLite preexistentes")
    for key in before_keys:
        if before[key] != after[key]:
            raise PreparationRejected(f"La migración alteró un objeto preexistente: {key[0]} {key[1]}")

    expected = {("table", name) for name in ENRICHMENT_TABLES}
    expected |= {("index", name) for name in ENRICHMENT_INDEXES}
    added = after_keys - before_keys
    if added != expected:
        raise PreparationRejected(f"La migración creó objetos no autorizados: {sorted(added ^ expected)}")
    return [f"{kind}:{name}" for kind, name in sorted(added)]


def _verify_preserved_tables(path: Path, source_inventory: dict[str, dict[str, Any]]) -> None:
    candidate_inventory = _table_inventory(path)
    expected_tables = set(source_inventory) | set(ENRICHMENT_TABLES)
    if set(candidate_inventory) != expected_tables:
        raise PreparationRejected("La candidata contiene tablas no previstas o falta alguna tabla existente")
    changed = [name for name, previous in source_inventory.items() if candidate_inventory[name] != previous]
    if changed:
        raise PreparationRejected(f"La migración o enrichment alteró filas o valores existentes: {changed[:10]}")


def _selected_capture_counts(path: Path, version: str) -> dict[str, int]:
    # Reuse the store's exact selected-Spanish predicate and coverage validator.
    from enrichment.store import (  # local import documents and preserves the single source of selection semantics
        SELECTED_SPANISH_CAPTURE_PREDICATE,
        ensure_selected_enrichments_complete,
    )

    with closing(sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        eligible = connection.execute(
            "SELECT count(*) FROM narrative_comment_selection AS selection "
            "JOIN narrative_comment_capture AS capture ON capture.id = selection.capture_id "
            "AND capture.match_id = selection.match_id WHERE " + SELECTED_SPANISH_CAPTURE_PREDICATE
        ).fetchone()[0]
        empty_captures = connection.execute(
            "SELECT count(*) FROM narrative_comment_selection AS selection "
            "JOIN narrative_comment_capture AS capture ON capture.id = selection.capture_id "
            "AND capture.match_id = selection.match_id WHERE " + SELECTED_SPANISH_CAPTURE_PREDICATE +
            " AND capture.availability = 'empty'"
        ).fetchone()[0]
        comments = connection.execute(
            "SELECT count(*) FROM narrative_comment AS comment "
            "JOIN narrative_comment_selection AS selection ON selection.capture_id = comment.capture_id "
            "JOIN narrative_comment_capture AS capture ON capture.id = selection.capture_id "
            "AND capture.match_id = selection.match_id WHERE " + SELECTED_SPANISH_CAPTURE_PREDICATE +
            " AND capture.availability = 'available'"
        ).fetchone()[0]
        annotations = connection.execute(
            "SELECT count(*) FROM narrative_comment_enrichment AS enrichment "
            "JOIN narrative_comment_selection AS selection ON selection.capture_id = enrichment.capture_id "
            "JOIN narrative_comment_capture AS capture ON capture.id = selection.capture_id "
            "AND capture.match_id = selection.match_id WHERE " + SELECTED_SPANISH_CAPTURE_PREDICATE +
            " AND enrichment.enrichment_version = ?",
            (version,),
        ).fetchone()[0]
        participants = connection.execute(
            "SELECT count(*) FROM narrative_comment_enrichment_participant AS participant "
            "JOIN narrative_comment_selection AS selection ON selection.capture_id = participant.capture_id "
            "JOIN narrative_comment_capture AS capture ON capture.id = selection.capture_id "
            "AND capture.match_id = selection.match_id WHERE " + SELECTED_SPANISH_CAPTURE_PREDICATE +
            " AND participant.enrichment_version = ?",
            (version,),
        ).fetchone()[0]
        ensure_selected_enrichments_complete(connection, version)
    return {
        "selected_spanish_captures": eligible,
        "selected_empty_captures": empty_captures,
        "selected_available_comments": comments,
        "selected_annotations": annotations,
        "selected_participants": participants,
    }


def _write_private_json(path: Path, value: Any) -> None:
    raw = _json_bytes(value)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _reject_unverified_package_source(source: Path) -> None:
    updates = Path(str(source) + ".actualizaciones")
    payload = next((parent for parent in source.parents if parent.name == "payload"), None)
    manifest = (payload.parent / "manifest.json") if payload is not None else (source.parent / "manifest.json")
    if updates.exists() or manifest.is_file():
        raise PreparationRejected(
            "El origen pertenece a un árbol con journal o manifiesto; esta herramienta solo acepta una SQLite "
            "aislada y no valida paquetes coherentes de coordinador"
        )


def _validate_paths(source: Path, output_dir: Path) -> tuple[Path, Path]:
    source = Path(source).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    if not source.is_file():
        raise PreparationRejected("--source debe indicar una SQLite existente, no un directorio o paquete")
    _reject_unverified_package_source(source)
    if output_dir == source or _within(source, output_dir):
        raise PreparationRejected("El directorio de salida no puede coincidir con ni contener la SQLite de origen")
    protected_roots = (ROOT.resolve(), source.parent)
    if any(_within(output_dir, protected) for protected in protected_roots):
        raise PreparationRejected("La salida debe quedar fuera del repositorio y del árbol que contiene el origen")
    if output_dir.exists():
        raise PreparationRejected(f"El directorio de salida ya existe: {output_dir}")
    if not output_dir.parent.is_dir():
        raise PreparationRejected("El directorio padre de la salida debe existir")
    return source, output_dir


def prepare_candidate(source: str | Path, output_dir: str | Path) -> dict[str, Any]:
    """Create and verify a private 0008 candidate without changing the standalone source SQLite."""
    source, output_dir = _validate_paths(Path(source), Path(output_dir))
    output_dir.mkdir(mode=0o700)
    os.chmod(output_dir, 0o700)
    candidate = output_dir / CANDIDATE_NAME
    committed = False
    try:
        with bloquear(source):
            source_before = _description(source)
            if sqlite.revision(source) != SOURCE_REVISION:
                raise PreparationRejected(f"La SQLite de origen debe estar en {SOURCE_REVISION}")
            source_inventory = _table_inventory(source)
            source_file_hash_before = _sha256_file(source)

            sqlite.copia_consistente(source, candidate)
            os.chmod(candidate, 0o600)
            copied_description = sqlite.comprobar_copia(candidate, source_before)
            source_after_copy = _description(source)
            source_file_hash_after = _sha256_file(source)
            if source_after_copy != source_before or source_file_hash_after != source_file_hash_before:
                raise PreparationRejected("El origen cambió durante la copia; se descarta la candidata")
            if copied_description != source_before:
                raise PreparationRejected("La copia consistente no coincide con la descripción del origen")

        original_objects = _schema_objects(candidate)
        migration_config = carga.configuracion_alembic(candidate)
        command.upgrade(migration_config, TARGET_REVISION)
        os.chmod(candidate, 0o600)
        migrated_description = _description(candidate)
        if migrated_description["revision"] != TARGET_REVISION:
            raise PreparationRejected("La migración no dejó la candidata en Alembic 0008")

        migrated_objects = _schema_objects(candidate)
        added_objects = _verify_schema_transition(original_objects, migrated_objects)
        _verify_enrichment_schema(candidate)
        _verify_preserved_tables(candidate, source_inventory)

        first_run = persist_candidate_enrichments(candidate, DEFAULT_ENRICHMENT_VERSION)
        second_run = persist_candidate_enrichments(candidate, DEFAULT_ENRICHMENT_VERSION)
        if first_run["inserted"] != second_run["unchanged"] or second_run["inserted"] != 0:
            raise PreparationRejected("rules-v1 no pasó la comprobación de idempotencia")
        _verify_preserved_tables(candidate, source_inventory)

        coverage = _selected_capture_counts(candidate, DEFAULT_ENRICHMENT_VERSION)
        if coverage["selected_available_comments"] != coverage["selected_annotations"]:
            raise PreparationRejected("La cobertura rules-v1 no coincide con los comentarios seleccionados")
        final_description = _description(candidate)
        if final_description["revision"] != TARGET_REVISION:
            raise PreparationRejected("La revisión de candidata cambió tras el enriquecimiento")

        # Close/normalize SQLite's write journal on the candidate only, then verify no sidecars remain.
        with closing(sqlite3.connect(candidate)) as connection:
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0].lower()
            if mode == "wal":
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.execute("PRAGMA journal_mode = DELETE")
        leftovers = [str(candidate) + suffix for suffix in sqlite.ACOMPANANTES if Path(str(candidate) + suffix).exists()]
        if leftovers:
            raise PreparationRejected(f"La candidata mantiene journals abiertos: {leftovers}")
        closed_description = _description(candidate)
        if closed_description != final_description:
            raise PreparationRejected("La candidata cambió al cerrar su journal SQLite")
        final_description = closed_description
        os.chmod(candidate, 0o600)
        candidate_hash = _sha256_file(candidate)

        reference = carga.referencia_del_codigo()
        report = {
            "format": "real-jaen-enrichment-candidate-prepare-v1",
            "status": "candidate_not_installed",
            "source_kind": "standalone_sqlite; coordinator package/journals were not supplied or validated",
            "source_path": str(source),
            "candidate_path": str(candidate),
            "candidate_installed": False,
            "source_revision": SOURCE_REVISION,
            "target_revision": TARGET_REVISION,
            "enrichment_version": DEFAULT_ENRICHMENT_VERSION,
            "code_reference": {
                "text": reference["texto"],
                "commit": reference["commit"],
                "sha256_agregada": reference["sha256_agregada"],
            },
            "source_semantic_description": source_before,
            "candidate_semantic_description": final_description,
            "source_snapshot_semantic_sha256": source_before["huella"],
            "candidate_sqlite_sha256": candidate_hash,
            "source_main_file_sha256_before_copy": source_file_hash_before,
            "source_main_file_sha256_after_copy": source_file_hash_after,
            "preexisting_tables_preserved": True,
            "preexisting_table_count": len(source_inventory),
            "preexisting_tables": source_inventory,
            "schema_objects_added_by_0008": added_objects,
            "enrichment": {
                "first_run": first_run,
                "idempotency_run": second_run,
                **coverage,
                "coverage_complete": True,
            },
            "validation": {
                "source_integrity_check": source_before["integridad"],
                "source_foreign_key_violations": len(source_before["claves_foraneas"]),
                "candidate_integrity_check": final_description["integridad"],
                "candidate_foreign_key_violations": len(final_description["claves_foraneas"]),
                "source_unchanged_during_copy": True,
                "existing_rows_and_values_preserved": True,
                "only_0008_schema_objects_added": True,
                "rules_v1_idempotent": True,
                "candidate_sidecars_absent": True,
            },
            "limitations": [
                "No se validó journal, manifiesto ni paquete coherente de coordinador; el origen fue una SQLite suelta.",
                "Esta candidata no se instala y no adopta ni modifica ninguna huella confirmada.",
                "Una futura instalación debe verificar la descripción y la huella realmente confirmadas en ese momento.",
            ],
        }
        _write_private_json(output_dir / REPORT_NAME, report)
        committed = True
        return report
    except BaseException:
        if not committed:
            try:
                shutil.rmtree(output_dir)
            except OSError as cleanup_error:
                raise PreparationRejected(
                    f"Falló la preparación y no se pudo limpiar la salida parcial {output_dir}: {cleanup_error}"
                ) from cleanup_error
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    prepare = subparsers.add_parser("prepare", help="crear una candidata privada 0007 -> 0008 no instalada")
    prepare.add_argument("--source", required=True, type=Path, help="ruta de la SQLite suelta en revisión 0007")
    prepare.add_argument("--output-dir", required=True, type=Path, help="directorio privado nuevo, fuera del árbol de origen")
    args = parser.parse_args(argv)
    try:
        report = prepare_candidate(args.source, args.output_dir)
    except (PreparationRejected, OSError, sqlite.CopiaNoValida) as error:
        parser.exit(2, f"prepare rechazado: {error}\n")
    print(json.dumps({
        "status": report["status"],
        "candidate_path": report["candidate_path"],
        "report_path": str(Path(args.output_dir).resolve() / REPORT_NAME),
        "revision": report["target_revision"],
        "candidate_sqlite_sha256": report["candidate_sqlite_sha256"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
