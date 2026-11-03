"""Import and read the separate, versioned store for narrative match comments.

These captures are deliberately outside `originales` and the sports section availability contract. Provider player
IDs remain evidence and are never resolved to local player identities here.
"""
from __future__ import annotations

import hashlib
import base64
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from persistencia.bloqueo import bloquear

PROVIDER = 'sofascore'
PUBLIC_MATCH_PREFIX = 'sofascore-match-'
EVENT_ID = '16600011'
EXPECTED_RESPONSE_SHA256 = 'e2eb4e64d75ced718cc1f329b90a51cefd804a9bd71993a7b96532736ada85bb'
COMMENT_PARTICIPANTS = ('player', 'playerIn', 'playerOut', 'assist1')
COMMENT_CONTEXT = ('injury', 'footballPassingNetworkAction')


class NarrativeCommentImportRejected(ValueError):
    """The saved response is not the reviewed, valid response for the local event."""


def _strict_json(text: str):
    def reject_constant(value):
        raise NarrativeCommentImportRejected(f'Constante JSON no válida: {value}')
    try:
        return json.loads(text, parse_constant=reject_constant)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise NarrativeCommentImportRejected(f'La respuesta no es JSON UTF-8 válido: {error}') from error


def _optional_string(value, field):
    if value is None:
        return None
    if not isinstance(value, str):
        raise NarrativeCommentImportRejected(f'{field} debe ser texto o null')
    return value


def _optional_integer(value, field, *, minimum=0):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise NarrativeCommentImportRejected(f'{field} debe ser un entero válido o null')
    return value


def _comment_rows(document):
    if not isinstance(document, dict) or not isinstance(document.get('comments'), list):
        raise NarrativeCommentImportRejected('Se esperaba un objeto con una lista comments')
    seen = set()
    rows = []
    for position, item in enumerate(document['comments']):
        if not isinstance(item, dict):
            raise NarrativeCommentImportRejected(f'comments[{position}] no es un objeto')
        source_id = item.get('id')
        if isinstance(source_id, bool) or not isinstance(source_id, (int, str)) or str(source_id).strip() == '':
            raise NarrativeCommentImportRejected(f'comments[{position}].id no es un identificador válido')
        source_id = str(source_id)
        if source_id in seen:
            raise NarrativeCommentImportRejected(f'Identificador de comentario repetido: {source_id}')
        seen.add(source_id)
        text = item.get('text')
        kind = item.get('type')
        if not isinstance(text, str) or not text.strip():
            raise NarrativeCommentImportRejected(f'comments[{position}].text está vacío o no es texto')
        if not isinstance(kind, str) or not kind.strip():
            raise NarrativeCommentImportRejected(f'comments[{position}].type está vacío o no es texto')
        minute = _optional_integer(item.get('time'), f'comments[{position}].time')
        period = _optional_string(item.get('periodName'), f'comments[{position}].periodName')
        is_home = item.get('isHome')
        if is_home is not None and not isinstance(is_home, bool):
            raise NarrativeCommentImportRejected(f'comments[{position}].isHome debe ser booleano o null')
        home_score = _optional_integer(item.get('homeScore'), f'comments[{position}].homeScore')
        away_score = _optional_integer(item.get('awayScore'), f'comments[{position}].awayScore')
        incident_id = item.get('incidentId')
        if incident_id is not None and (isinstance(incident_id, bool) or not isinstance(incident_id, (int, str))):
            raise NarrativeCommentImportRejected(f'comments[{position}].incidentId no es válido')
        participants = {}
        for field in COMMENT_PARTICIPANTS:
            value = item.get(field)
            if value is not None and not isinstance(value, dict):
                raise NarrativeCommentImportRejected(f'comments[{position}].{field} debe ser objeto o null')
            if field in item:
                participants[field] = value
        context = {field: item[field] for field in COMMENT_CONTEXT if field in item}
        rows.append((source_id, position, text, kind, minute, period, is_home, home_score, away_score,
                     str(incident_id) if incident_id is not None else None,
                     json.dumps(participants, ensure_ascii=False, separators=(',', ':'), allow_nan=False),
                     json.dumps(context, ensure_ascii=False, separators=(',', ':'), allow_nan=False)))
    return rows


def validar_respuesta(response: bytes) -> int:
    """Validate the JSON shape and normalized comment fields without writing them."""
    try:
        document = _strict_json(response.decode('utf-8'))
    except UnicodeDecodeError as error:
        raise NarrativeCommentImportRejected('La respuesta no está codificada como UTF-8') from error
    return len(_comment_rows(document))


def captured_at_from_file(path: Path) -> str:
    """Filesystem capture timestamp in UTC; the SofaScore payload itself has no acquisition timestamp."""
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec='microseconds')


def _event_match(connection):
    rows = connection.execute("""
        SELECT m.id, m.status, ms.status, ms.round
        FROM match_external_id AS external
        JOIN match AS m ON m.id = external.entity_id
        JOIN match_state AS ms ON ms.match_id = m.id
        WHERE external.provider = ? AND external.namespace = '' AND external.external_id = ?
          AND external.status IN ('origin', 'verified')
    """, (PROVIDER, EVENT_ID)).fetchall()
    if len(rows) != 1:
        raise NarrativeCommentImportRejected(f'El evento {EVENT_ID} no tiene una única identidad local aprobada')
    match_id, entity_status, match_status, round_number = rows[0]
    if entity_status != 'active' or match_status != 'finished' or str(round_number) != '10':
        raise NarrativeCommentImportRejected('El evento local no está activo, finalizado y en la jornada 10')
    return match_id


def _scoped_event_match(connection, event_id: str, scope_pairs: set[tuple[str, str]]) -> int:
    """Resolve only an existing, active, finished Group 2 event in the configured season scope."""
    if not re.fullmatch(r'[1-9][0-9]{0,11}', str(event_id)) or not scope_pairs:
        raise NarrativeCommentImportRejected('Evento o ámbito de comentarios no válido')
    pairs = sorted((str(tournament), str(season)) for tournament, season in scope_pairs)
    placeholders = ','.join('(?, ?)' for _ in pairs)
    args = [PROVIDER, str(event_id)] + [value for pair in pairs for value in pair]
    rows = connection.execute(f"""
        SELECT m.id
        FROM match_external_id AS external
        JOIN match AS m ON m.id = external.entity_id
        JOIN match_state AS ms ON ms.match_id = m.id
        WHERE external.provider = ? AND external.namespace = '' AND external.external_id = ?
          AND external.status IN ('origin', 'verified')
          AND m.status = 'active' AND ms.status = 'finished'
          AND (ms.external_stage_id, ms.external_season_id) IN ({placeholders})
          AND NOT EXISTS (
              SELECT 1 FROM local_replacement AS replacement
              WHERE replacement.replaced_match_id = m.id AND replacement.status = 'active'
          )
    """, args).fetchall()
    if len(rows) != 1:
        raise NarrativeCommentImportRejected('El evento no es un partido finalizado y vigente del Grupo 2 configurado')
    return rows[0][0]


def record_http_attempt(connection, *, event_id: str, scope_pairs: set[tuple[str, str]], http_status: int | None,
                        response: bytes | None, source_path: str, captured_at: str,
                        transport_report: dict | None = None, error_code: str | None = None) -> dict:
    """Record one completed comment request in an existing SQLite transaction.

    Only a structurally valid HTTP 200 creates an immutable capture. Empty, missing or failed responses update the
    last-attempt fields while retaining the selected non-empty capture.
    """
    match_id = _scoped_event_match(connection, event_id, scope_pairs)
    timestamp = _validated_timestamp(captured_at)
    expected_path = f'/event/{event_id}/comments/es-la'
    if source_path != expected_path:
        raise NarrativeCommentImportRejected('Ruta de origen distinta del endpoint español autorizado')
    if http_status == 200 and response is not None:
        digest = hashlib.sha256(response).hexdigest()
        try:
            original_json = response.decode('utf-8')
        except UnicodeDecodeError as error:
            raise NarrativeCommentImportRejected('La respuesta no está codificada como UTF-8') from error
        document = _strict_json(original_json)
        rows = _comment_rows(document)
        availability = 'available' if rows else 'empty'
        existing = connection.execute("""
            SELECT id, response_json, availability
            FROM narrative_comment_capture WHERE match_id = ? AND response_sha256 = ?
        """, (match_id, digest)).fetchone()
        if existing:
            capture_id, saved_json, saved_availability = existing
            if saved_json != original_json or saved_availability != availability:
                raise NarrativeCommentImportRejected('La respuesta duplicada no coincide byte a byte con la captura guardada')
            inserted_capture = False
        else:
            report_json = json.dumps(transport_report or {}, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            cursor = connection.execute("""
                INSERT INTO narrative_comment_capture
                    (match_id, provider, source_event_id, source_path, captured_at, http_status, availability,
                     comment_count, response_sha256, response_json, transport_report_json)
                VALUES (?, ?, ?, ?, ?, 200, ?, ?, ?, ?, ?)
            """, (match_id, PROVIDER, event_id, source_path, timestamp, availability, len(rows), digest,
                  original_json, report_json))
            capture_id = cursor.lastrowid
            connection.executemany("""
                INSERT INTO narrative_comment
                    (capture_id, source_comment_id, source_position, text, type, minute, period_name, is_home,
                     home_score, away_score, incident_id, participants_json, context_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [(capture_id, *row) for row in rows])
            inserted_capture = True

        prior = connection.execute(
            'SELECT capture_id FROM narrative_comment_selection WHERE match_id = ?', (match_id,)
        ).fetchone()
        prior_capture_id = prior[0] if prior else None
        prior_has_comments = False
        if prior_capture_id is not None:
            prior_has_comments = connection.execute(
                "SELECT availability = 'available' FROM narrative_comment_capture WHERE id = ?",
                (prior_capture_id,),
            ).fetchone()[0]
        selected_capture_id = prior_capture_id if not rows and prior_has_comments else capture_id
        attempt_state, saved_status, saved_error = availability, 200, None
        successful_source_path = source_path
        result = dict(match_id=match_id, capture_id=capture_id, captured_new_response=inserted_capture,
                      availability=availability, comments=len(rows), response_sha256=digest,
                      captured_at=timestamp, selected_capture_id=selected_capture_id)
    else:
        prior = connection.execute(
            'SELECT capture_id FROM narrative_comment_selection WHERE match_id = ?', (match_id,)
        ).fetchone()
        selected_capture_id = prior[0] if prior else None
        attempt_state = 'error'
        saved_status = http_status
        saved_error = (error_code or ('http_error' if http_status is not None else 'transport_error'))[:80]
        successful_source_path = None
        result = dict(match_id=match_id, capture_id=None, captured_new_response=False, availability='error',
                      comments=0, response_sha256=None, captured_at=timestamp,
                      selected_capture_id=selected_capture_id, error_code=saved_error)

    connection.execute("""
        INSERT INTO narrative_comment_selection
            (match_id, capture_id, last_successful_source_path, last_attempt_state, last_attempt_at,
             last_http_status, last_error_code)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(match_id) DO UPDATE SET
            capture_id = excluded.capture_id,
            last_successful_source_path = COALESCE(excluded.last_successful_source_path,
                                                   narrative_comment_selection.last_successful_source_path),
            last_attempt_state = excluded.last_attempt_state,
            last_attempt_at = excluded.last_attempt_at,
            last_http_status = excluded.last_http_status,
            last_error_code = excluded.last_error_code
    """, (match_id, selected_capture_id, successful_source_path, attempt_state, timestamp, saved_status, saved_error))
    return result


def _validated_timestamp(timestamp: str) -> str:
    try:
        parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    except (AttributeError, ValueError) as error:
        raise NarrativeCommentImportRejected('captured_at debe ser una fecha ISO-8601') from error
    if parsed.tzinfo is None:
        raise NarrativeCommentImportRejected('captured_at debe incluir zona horaria')
    return timestamp


def import_saved_response(base: Path, response_path: Path, expected_sha256: str,
                          *, captured_at: str | None = None) -> dict:
    """Import one checked local HTTP 200 response into an already migrated isolated database."""
    base, response_path = Path(base).resolve(), Path(response_path).resolve()
    raw = response_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 != EXPECTED_RESPONSE_SHA256 or digest != expected_sha256:
        raise NarrativeCommentImportRejected('SHA-256 distinto de la evidencia HTTP 200 autorizada')
    try:
        original_json = raw.decode('utf-8')
    except UnicodeDecodeError as error:
        raise NarrativeCommentImportRejected('La evidencia no está codificada como UTF-8') from error
    document = _strict_json(original_json)
    rows = _comment_rows(document)
    timestamp = captured_at or captured_at_from_file(response_path)
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    except (AttributeError, ValueError) as error:
        raise NarrativeCommentImportRejected('captured_at debe ser una fecha ISO-8601') from error
    if parsed_timestamp.tzinfo is None:
        raise NarrativeCommentImportRejected('captured_at debe incluir zona horaria')

    with bloquear(base), closing(sqlite3.connect(base, timeout=10)) as connection:
        connection.execute('PRAGMA foreign_keys = ON')
        if connection.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
            raise NarrativeCommentImportRejected('La conexión de escritura no activa claves foráneas')
        connection.execute('BEGIN IMMEDIATE')
        try:
            match_id = _event_match(connection)
            availability = 'available' if rows else 'empty'
            existing = connection.execute("""
                SELECT id, response_json, availability
                FROM narrative_comment_capture WHERE match_id = ? AND response_sha256 = ?
            """, (match_id, digest)).fetchone()
            if existing:
                capture_id, saved_json, saved_availability = existing
                if saved_json != original_json or saved_availability != availability:
                    raise NarrativeCommentImportRejected('La respuesta duplicada no coincide byte a byte con la captura guardada')
                inserted_capture = False
            else:
                cursor = connection.execute("""
                    INSERT INTO narrative_comment_capture
                        (match_id, provider, source_event_id, captured_at, http_status, availability,
                         comment_count, response_sha256, response_json)
                    VALUES (?, ?, ?, ?, 200, ?, ?, ?, ?)
                """, (match_id, PROVIDER, EVENT_ID, timestamp, availability, len(rows), digest, original_json))
                capture_id = cursor.lastrowid
                connection.executemany("""
                    INSERT INTO narrative_comment
                        (capture_id, source_comment_id, source_position, text, type, minute, period_name, is_home,
                         home_score, away_score, incident_id, participants_json, context_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [(capture_id, *row) for row in rows])
                inserted_capture = True

            prior = connection.execute(
                'SELECT capture_id FROM narrative_comment_selection WHERE match_id = ?', (match_id,)
            ).fetchone()
            prior_capture_id = prior[0] if prior else None
            prior_has_comments = False
            if prior_capture_id is not None:
                prior_has_comments = connection.execute(
                    'SELECT availability = \'available\' FROM narrative_comment_capture WHERE id = ?',
                    (prior_capture_id,),
                ).fetchone()[0]
            # An empty response is retained, but cannot replace an already selected non-empty capture.
            selected_capture_id = (prior_capture_id if not rows and prior_has_comments else capture_id)
            connection.execute("""
                INSERT INTO narrative_comment_selection
                    (match_id, capture_id, last_attempt_state, last_attempt_at, last_http_status, last_error_code)
                VALUES (?, ?, ?, ?, 200, NULL)
                ON CONFLICT(match_id) DO UPDATE SET
                    capture_id = excluded.capture_id,
                    last_attempt_state = excluded.last_attempt_state,
                    last_attempt_at = excluded.last_attempt_at,
                    last_http_status = excluded.last_http_status,
                    last_error_code = NULL
            """, (match_id, selected_capture_id, availability, timestamp))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return {"match_id": match_id, "capture_id": capture_id, "captured_new_response": inserted_capture,
            "availability": availability, "comments": len(rows), "response_sha256": digest,
            "captured_at": timestamp, "selected_capture_id": selected_capture_id}


def load_current_comments(base: Path) -> dict[str, dict]:
    """Load selected comments as a snapshot. Callers retain this value for a whole published generation."""
    base = Path(base).resolve()
    output = {}
    with bloquear(base), closing(sqlite3.connect(base.as_uri() + '?mode=ro', uri=True, timeout=10)) as connection:
        connection.row_factory = sqlite3.Row
        capture_columns = {row[1] for row in connection.execute('PRAGMA table_info(narrative_comment_capture)')}
        selection_columns = {row[1] for row in connection.execute('PRAGMA table_info(narrative_comment_selection)')}
        if not capture_columns or not selection_columns:
            return output
        # The selected capture can intentionally remain an older non-empty response after an empty Spanish
        # response. Keep its provenance separate from the most recent successful request path.
        source_path_sql = ('capture.source_path' if 'source_path' in capture_columns else
                           ('selection.last_successful_source_path'
                            if 'last_successful_source_path' in selection_columns else 'NULL'))
        spanish_capture_sql = ("EXISTS (SELECT 1 FROM narrative_comment_capture AS spanish "
                               "WHERE spanish.match_id = selection.match_id "
                               "AND spanish.source_path = '/event/' || external.external_id || '/comments/es-la' "
                               "AND spanish.availability IN ('available', 'empty'))"
                               if 'source_path' in capture_columns else '0')
        rows = connection.execute(f"""
            SELECT external.external_id, selection.capture_id, selection.last_attempt_state,
                   selection.last_attempt_at, selection.last_http_status, selection.last_error_code,
                   capture.availability, capture.captured_at, capture.response_sha256, {source_path_sql} AS source_path,
                   {spanish_capture_sql} AS spanish_complete
            FROM narrative_comment_selection AS selection
            JOIN match_external_id AS external ON external.entity_id = selection.match_id
            LEFT JOIN narrative_comment_capture AS capture
              ON capture.id = selection.capture_id AND capture.match_id = selection.match_id
            WHERE external.provider = 'sofascore' AND external.namespace = ''
              AND external.status IN ('origin', 'verified')
        """).fetchall()
        for row in rows:
            comments = []
            if row['capture_id'] is not None:
                comment_rows = connection.execute("""
                    SELECT source_comment_id, source_position, text, type, minute, period_name, is_home,
                           home_score, away_score, incident_id, participants_json
                    FROM narrative_comment WHERE capture_id = ? ORDER BY source_position
                """, (row['capture_id'],)).fetchall()
                for comment in comment_rows:
                    home_score, away_score = comment['home_score'], comment['away_score']
                    score = ({'home': home_score, 'away': away_score}
                             if home_score is not None or away_score is not None else None)
                    comments.append({
                        'id': comment['source_comment_id'], 'position': comment['source_position'],
                        'text': comment['text'], 'type': comment['type'], 'minute': comment['minute'],
                        'periodName': comment['period_name'],
                        'isHome': bool(comment['is_home']) if comment['is_home'] is not None else None,
                        'score': score, 'incidentId': comment['incident_id'],
                        'participants': _public_participants(json.loads(comment['participants_json'])),
                    })
            current_availability = row['availability'] or row['last_attempt_state'] or 'not_collected'
            output[PUBLIC_MATCH_PREFIX + row['external_id']] = {
                'availability': current_availability,
                'sourcePath': row['source_path'],  # internal selection hint; removed from the REST response below
                'spanishComplete': bool(row['spanish_complete']),  # internal acquisition hint, not public API data
                'capturedAt': row['captured_at'],
                'responseSha256': row['response_sha256'],
                'lastAttempt': {
                    'availability': row['last_attempt_state'], 'occurredAt': row['last_attempt_at'],
                    'httpStatus': row['last_http_status'], 'errorCode': row['last_error_code'],
                },
                'comments': comments,
            }
    return output


def comments_schema_ready(base: Path) -> bool:
    """True only when the existing narrative-capture and provenance migrations are both present."""
    base = Path(base).resolve()
    with bloquear(base), closing(sqlite3.connect(base.as_uri() + '?mode=ro', uri=True, timeout=10)) as connection:
        capture = {row[1] for row in connection.execute('PRAGMA table_info(narrative_comment_capture)')}
        selection = {row[1] for row in connection.execute('PRAGMA table_info(narrative_comment_selection)')}
    return ({'source_path', 'transport_report_json'} <= capture
            and 'last_successful_source_path' in selection)


def _public_participants(values: dict) -> dict:
    output = {}
    for role in COMMENT_PARTICIPANTS:
        participant = values.get(role)
        if not isinstance(participant, dict):
            continue
        output[role] = {
            'name': participant.get('name') if isinstance(participant.get('name'), str) else None,
            'shortName': participant.get('shortName') if isinstance(participant.get('shortName'), str) else None,
            'position': participant.get('position') if isinstance(participant.get('position'), str) else None,
            'jerseyNumber': participant.get('jerseyNumber') if isinstance(participant.get('jerseyNumber'), str) else None,
        }
    return output


def match_comments_response(data: dict, snapshots: dict[str, dict], public_match_id: str):
    """Resolve a public match ID against this sports generation and return its separate comments snapshot."""
    matched = re.fullmatch(r'sofascore-match-([1-9]\d*)', public_match_id)
    if not matched:
        return 404, {'error': 'Partido no encontrado'}
    from queries import consultar
    source_id = matched.group(1)
    status, _ = consultar(data, f'/api/v1/matches/{source_id}')
    if status != 200:
        return status, {'error': 'Partido no encontrado'}
    snapshot = snapshots.get(public_match_id)
    if snapshot is None:
        snapshot = {'availability': 'not_collected', 'capturedAt': None, 'responseSha256': None,
                    'lastAttempt': {'availability': 'not_collected', 'occurredAt': None,
                                    'httpStatus': None, 'errorCode': None}, 'comments': []}
    public_snapshot = {key: value for key, value in snapshot.items()
                       if key not in ('sourcePath', 'spanishComplete')}
    return 200, {'matchId': public_match_id, **json.loads(json.dumps(public_snapshot, ensure_ascii=False, allow_nan=False))}


def import_group_bundle(base: Path, response_file: Path) -> dict:
    """Apply verified Group 2 comment attempts to the unpublished coordinator candidate only."""
    response_file=Path(response_file)
    records=[json.loads(line) for line in response_file.read_bytes().splitlines()]
    attempted={row['event_id'] for row in records}
    if not records:
        return {'captured_ids': [], 'failed_ids': []}
    if not comments_schema_ready(base):
        return {'captured_ids': [], 'failed_ids': sorted(attempted)}
    connection=sqlite3.connect(base,timeout=10)
    captured=set();failed=set()
    try:
        connection.execute('PRAGMA foreign_keys = ON')
        if connection.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
            raise NarrativeCommentImportRejected('SQLite foreign keys unavailable')
        connection.execute('BEGIN IMMEDIATE')
        for index,row in enumerate(records):
            ident=row['event_id']
            savepoint=f'comment_{index}'
            connection.execute(f'SAVEPOINT {savepoint}')
            try:
                raw=base64.b64decode(row['raw_b64'],validate=True) if row['raw_b64'] is not None else None
                if raw is not None and hashlib.sha256(raw).hexdigest()!=row['raw_sha256']:
                    raise NarrativeCommentImportRejected('Comments response hash mismatch')
                status=row['status'] if type(row['status']) is int and 100<=row['status']<=599 else None
                error_code=row['reason']
                response=None
                if status==200 and raw is not None:
                    try:
                        validar_respuesta(raw)
                        response=raw
                        error_code=None
                    except NarrativeCommentImportRejected:
                        error_code='invalid_response'
                report=dict(row['transport_report'])
                record_http_attempt(connection,event_id=str(ident),scope_pairs={('94191','97382')},
                                    http_status=status,response=response,
                                    source_path=f'/event/{ident}/comments/es-la',captured_at=row['captured_at'],
                                    transport_report=report,error_code=error_code)
                connection.execute(f'RELEASE {savepoint}')
                if status==200 and response is not None:
                    captured.add(ident)
            except Exception:
                connection.execute(f'ROLLBACK TO {savepoint}')
                connection.execute(f'RELEASE {savepoint}')
                failed.add(ident)
        connection.commit()
    except Exception:
        connection.rollback()
        failed.update(attempted)
        captured.clear()
    finally:
        connection.close()
    return {'captured_ids':sorted(captured),'failed_ids':sorted(failed)}
