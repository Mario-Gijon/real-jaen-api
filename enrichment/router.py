"""Isolated FastAPI routes for read-only narrative-comment enrichment."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .queries import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    EnrichmentQueryError,
    open_readonly_connection,
    query_enriched_comments,
)
from .store import DEFAULT_ENRICHMENT_VERSION


router = APIRouter(tags=["enrichment"])


def get_enrichment_database_path(request: Request) -> Path:
    """Resolve the database explicitly configured on this application.

    Tests and future application wiring may override this dependency. The
    main sports-data app intentionally does not install a database path yet.
    """
    database_path = getattr(request.app.state, "enrichment_database_path", None)
    if database_path is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "database_not_configured"},
        )
    return Path(database_path)


def _validate_enrichment_database(connection: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    required = {
        "alembic_version",
        "narrative_comment_enrichment",
        "narrative_comment_enrichment_participant",
    }
    if not required.issubset(tables):
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "migration_0008_required"},
        )

    try:
        revisions = {row[0] for row in connection.execute("SELECT version_num FROM alembic_version")}
    except sqlite3.Error as error:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "migration_0008_required"},
        ) from error
    if "0008" not in revisions:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "migration_0008_required"},
        )

    try:
        has_version = connection.execute(
            """SELECT 1 FROM narrative_comment_enrichment
               WHERE enrichment_version = ? LIMIT 1""",
            (DEFAULT_ENRICHMENT_VERSION,),
        ).fetchone()
    except sqlite3.Error as error:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "migration_0008_required"},
        ) from error
    if has_version is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "rules_v1_not_found"},
        )


def _parse_attributes(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise EnrichmentQueryError("attributes must be a JSON object") from error
    if not isinstance(decoded, dict):
        raise EnrichmentQueryError("attributes must be a JSON object")
    return decoded


@router.get("/api/v1/enrichment/comments")
def enriched_comments(
    request: Request,
    match_id: int | None = Query(default=None, gt=0),
    source_event_id: str | None = Query(default=None),
    player_id: str | None = Query(default=None),
    category: str | None = Query(default=None),
    original_type: str | None = Query(default=None),
    role: str | None = Query(default=None),
    attributes: str | None = Query(
        default=None,
        description='Filtro JSON, por ejemplo {"surface":"right_foot"}.',
    ),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    database_path: Path = Depends(get_enrichment_database_path),
) -> dict[str, Any]:
    """Query one page of selected Spanish comments enriched with rules-v1."""
    try:
        parsed_attributes = _parse_attributes(attributes)
    except EnrichmentQueryError as error:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_filter", "reason": str(error)},
        ) from error

    try:
        connection = open_readonly_connection(database_path)
    except (OSError, sqlite3.Error) as error:
        raise HTTPException(
            status_code=503,
            detail={"error": "enrichment_unavailable", "reason": "database_unavailable"},
        ) from error

    with closing(connection):
        try:
            _validate_enrichment_database(connection)
            return query_enriched_comments(
                connection,
                match_id=match_id,
                source_event_id=source_event_id,
                player_id=player_id,
                category=category,
                original_type=original_type,
                role=role,
                attributes=parsed_attributes,
                enrichment_version=DEFAULT_ENRICHMENT_VERSION,
                limit=limit,
                offset=offset,
            )
        except EnrichmentQueryError as error:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_filter", "reason": str(error)},
            ) from error
        except sqlite3.Error as error:
            raise HTTPException(
                status_code=503,
                detail={"error": "enrichment_unavailable", "reason": "database_unavailable"},
            ) from error


__all__ = ["get_enrichment_database_path", "router"]
