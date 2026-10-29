"""Guards of the single real run (docker/acquisition/real_group.py) before anything is created or sent: identified
copy only, limits that only lower production, declared rounds, confirmation, window and the one-time ledger."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'docker' / 'acquisition'))
real_group = importlib.import_module('real_group')

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc).timestamp()


class Guards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'copias').mkdir()
        self.copy = root / 'copias' / 'real-jaen.20261027-copia.sqlite'
        self.copy.write_bytes(b'identified copy')
        self.sha = hashlib.sha256(b'identified copy').hexdigest()
        real_group.LEDGER_DIR = root / 'ledger'

    def args(self, **over):
        values = dict(mode='real', snapshot=self.copy, snapshot_sha256=self.sha, max_attempts=60, max_sends=70,
                      detail_rounds=[9], evidence=Path(self.tmp.name) / 'evidence', confirm_real_run=True,
                      not_after='2026-10-30T19:00:00+00:00')
        values.update(over)
        return argparse.Namespace(**values)

    def refused(self, **over):
        with self.assertRaises(real_group.Refused) as caught:
            real_group.preflight(self.args(**over), NOW)
        return str(caught.exception)

    def test_identified_copy_only(self):
        active = Path(self.tmp.name) / 'copias' / 'real-jaen.sqlite'
        active.write_bytes(b'identified copy')
        self.assertIn('never the active base', self.refused(snapshot=active))
        outside = Path(self.tmp.name) / 'other.sqlite'
        outside.write_bytes(b'identified copy')
        self.assertIn('copias', self.refused(snapshot=outside))
        self.assertIn('another sha256', self.refused(snapshot_sha256='0' * 64))

    def test_limits_only_lower_production(self):
        for over in (dict(max_attempts=117, max_sends=120), dict(max_sends=121), dict(max_attempts=71), dict(max_attempts=0)):
            with self.subTest(over=over):
                self.assertIn('only lower', self.refused(**over))
        self.assertIn('rounds', self.refused(detail_rounds=[]))
        self.assertIn('rounds', self.refused(detail_rounds=[39]))

    def test_real_needs_confirmation_window_and_runs_once(self):
        self.assertIn('--confirm-real-run', self.refused(confirm_real_run=False))
        self.assertIn('Outside the window', self.refused(not_after='2026-10-30T11:00:00+00:00'))
        facts = real_group.preflight(self.args(), NOW)
        self.assertEqual((facts['detail_rounds'], facts['snapshot_sha256']), ([9], self.sha))
        ledger = Path(facts['ledger'])
        ledger.parent.mkdir(parents=True)
        ledger.write_text('{}')
        self.assertIn('never repeated', self.refused())
        # Rehearsals neither need nor touch the ledger.
        self.assertIsNone(real_group.preflight(self.args(mode='rehearsal', confirm_real_run=False), NOW)['ledger'])

    def test_evidence_folder_must_be_new(self):
        (Path(self.tmp.name) / 'evidence').mkdir()
        self.assertIn('evidence', self.refused())


if __name__ == '__main__':
    unittest.main()
