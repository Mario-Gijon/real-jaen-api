"""Focused ASGI checks for the isolated read-only enrichment router."""

import asyncio
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

try:
    from fastapi import FastAPI
except ImportError:
    raise unittest.SkipTest("instala requirements-fastapi.txt para las pruebas del router")

try:
    from alembic import command
    from alembic.config import Config
except ImportError:
    raise unittest.SkipTest("instala requirements-sql.txt para las pruebas del router")


ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS))

from enrichment.router import router  # noqa: E402
from test_enrichment_queries import EnrichmentQueryTests as QueryDatabaseFixture  # noqa: E402


async def _asgi_get(application, target: str):
    path, separator, query = target.partition("?")
    sent = []
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query.encode("ascii") if separator else b"",
        "headers": [(b"host", b"testserver")],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "root_path": "",
    }
    await application(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent
                    if message["type"] == "http.response.body")
    return start["status"], json.loads(body)


class EnrichmentRouterTests(unittest.TestCase):
    def setUp(self):
        fixture = QueryDatabaseFixture()
        fixture.setUp()
        fixture.connection.close()
        self.fixture = fixture
        self.database = fixture.database
        self.application = FastAPI()
        self.application.state.enrichment_database_path = self.database
        self.application.include_router(router)
        self.addCleanup(fixture.doCleanups)

    def request(self, **params):
        target = "/api/v1/enrichment/comments"
        if params:
            target += "?" + urlencode(params)
        return asyncio.run(_asgi_get(self.application, target))

    def test_player_combination_and_pagination(self):
        status, player_page = self.request(source_event_id="16600011", player_id="50", limit=10)
        self.assertEqual(status, 200)
        self.assertEqual(player_page["enrichment_version"], "rules-v1")
        self.assertEqual(player_page["total"], 1)
        self.assertEqual(player_page["items"][0]["comment"]["source_comment_id"], "goal-with-assist")

        status, wrong_role = self.request(match_id=1, player_id="50", role="scorer")
        self.assertEqual((status, wrong_role["total"]), (200, 0))
        status, correct_role = self.request(match_id=1, player_id="50", role="assistant")
        self.assertEqual((status, correct_role["total"]), (200, 1))

        status, first = self.request(match_id=1, limit=2, offset=0)
        next_status, second = self.request(match_id=1, limit=2, offset=2)
        self.assertEqual((status, next_status), (200, 200))
        self.assertEqual((first["total"], first["limit"], first["offset"]), (7, 2, 0))
        self.assertEqual((second["total"], second["limit"], second["offset"]), (7, 2, 2))
        self.assertEqual((len(first["items"]), len(second["items"])), (2, 2))

    def test_filter_validation_and_missing_migration_or_rules_are_controlled(self):
        status, invalid = self.request(attributes="[1,2]")
        self.assertEqual(status, 422)
        self.assertEqual(invalid["detail"]["error"], "invalid_filter")

        with tempfile.TemporaryDirectory(prefix="rj-enrichment-router-") as directory:
            old_revision_copy = Path(directory) / "old-revision.sqlite"
            shutil.copy2(self.database, old_revision_copy)
            with sqlite3.connect(old_revision_copy) as connection:
                connection.execute("UPDATE alembic_version SET version_num = '0007'")
            self.application.state.enrichment_database_path = old_revision_copy
            status, unavailable = self.request(match_id=1)
            self.assertEqual(status, 503)
            self.assertEqual(unavailable["detail"]["reason"], "migration_0008_required")

            no_rules_copy = Path(directory) / "no-rules.sqlite"
            shutil.copy2(self.database, no_rules_copy)
            with sqlite3.connect(no_rules_copy) as connection:
                connection.execute("DELETE FROM narrative_comment_enrichment_participant")
                connection.execute("DELETE FROM narrative_comment_enrichment")
            self.application.state.enrichment_database_path = no_rules_copy
            status, unavailable = self.request(match_id=1)
            self.assertEqual(status, 503)
            self.assertEqual(unavailable["detail"]["reason"], "rules_v1_not_found")


if __name__ == "__main__":
    unittest.main()
