"""Focused contract tests for narrative comments; no provider or database access."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api  # noqa: E402
from persistencia.comentarios import NarrativeCommentImportRejected, _comment_rows, match_comments_response  # noqa: E402


class NarrativeCommentNormalization(unittest.TestCase):
    def test_preserves_provider_fields_and_source_order_without_inventing_values(self):
        raw = [
            {
                "id": 2, "text": "Final", "type": "matchEnded", "time": None, "periodName": None,
                "isHome": None, "homeScore": None, "awayScore": None, "incidentId": None,
            },
            {
                "id": 1, "text": "Gol", "type": "scoreChange", "time": 97, "periodName": "2ND",
                "isHome": True, "homeScore": 3, "awayScore": 2, "incidentId": 55,
                "player": {"id": 1001, "name": "Jugador", "position": "F"},
                "footballPassingNetworkAction": {"action": "finish"},
            },
        ]
        rows = _comment_rows({"comments": raw})
        self.assertEqual([row[0] for row in rows], ["2", "1"])
        self.assertEqual([row[1] for row in rows], [0, 1])
        self.assertIsNone(rows[0][5])
        self.assertIsNone(rows[0][4])
        self.assertEqual(rows[1][4:10], (97, "2ND", True, 3, 2, "55"))
        self.assertIn('"id":1001', rows[1][10])
        self.assertIn("footballPassingNetworkAction", rows[1][11])

    def test_rejects_duplicate_ids_invalid_minutes_and_boolean_minutes(self):
        base = {"id": 1, "text": "Acción", "type": "shot"}
        with self.assertRaises(NarrativeCommentImportRejected):
            _comment_rows({"comments": [base, base]})
        with self.assertRaises(NarrativeCommentImportRejected):
            _comment_rows({"comments": [{**base, "time": -1}]})
        with self.assertRaises(NarrativeCommentImportRejected):
            _comment_rows({"comments": [{**base, "time": True}]})

    def test_public_endpoint_distinguishes_known_uncollected_from_unknown_match(self):
        fixture = Path(__file__).resolve().parent / "fixtures" / "datos"
        data = api.cargar(fixture)
        source_id = str(data["partidos"][0]["match_id"])
        public_id = f"sofascore-match-{source_id}"
        status, body = match_comments_response(data, {}, public_id)
        self.assertEqual(status, 200)
        self.assertEqual(body["availability"], "not_collected")
        self.assertEqual(body["comments"], [])
        status, body = match_comments_response(data, {}, f"sofascore-match-{int(source_id) + 999999}")
        self.assertEqual((status, body), (404, {"error": "Partido no encontrado"}))


if __name__ == "__main__":
    unittest.main()
