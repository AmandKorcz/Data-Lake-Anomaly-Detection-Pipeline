import json
import os
import subprocess
import sys
import tempfile
import unittest

from pathlib import Path

import pandas as pd

from src.alerts import (
    ke24_pretrained_csv_transaction as store,
)


class TestKE24PretrainedCSVRecovery(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        folder = Path(self.temp.name)

        self.history_file = folder / "history.csv"
        self.dashboard_file = folder / "dashboard.csv"

        self.history = pd.DataFrame({
            "alert_id": ["A1"],
            "investigation_status": ["PENDING"],
        })

        self.dashboard = pd.DataFrame({
            "alert_id": ["A1"],
            "alert_count": [1],
        })

    def publish(self, status="PENDING"):
        history = self.history.copy()

        history["investigation_status"] = status

        store.publish_pretrained_csv_pair(
            history,
            self.dashboard,
            self.history_file,
            self.dashboard_file,
        )

    def test_process_crash_after_first_replace_is_recovered(self):
        self.publish()

        old_history = self.history_file.read_bytes()
        old_dashboard = self.dashboard_file.read_bytes()

        # Child process simulates abrupt termination.
        script = '''
import os
import sys
from pathlib import Path
import pandas as pd
from src.alerts import ke24_pretrained_csv_transaction as store

history, dashboard = map(Path, sys.argv[1:3])
original = os.replace

def crash_after_history(source, destination):
    original(source, destination)

    if Path(destination).resolve() == history.resolve():
        os._exit(37)

store.os.replace = crash_after_history

store.publish_pretrained_csv_pair(
    pd.DataFrame({
        "alert_id": ["A1"],
        "investigation_status": ["INVESTIGATING"],
    }),
    pd.DataFrame({
        "alert_id": ["A1"],
        "alert_count": [2],
    }),
    history,
    dashboard,
)
'''

        run = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(self.history_file),
                str(self.dashboard_file),
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(
            run.returncode,
            37,
            run.stderr,
        )

        self.assertNotEqual(
            self.history_file.read_bytes(),
            old_history,
        )

        self.assertEqual(
            self.dashboard_file.read_bytes(),
            old_dashboard,
        )

        transaction = store._transaction_dir(
            self.history_file,
            self.dashboard_file,
        )

        self.assertTrue(
            transaction.exists()
        )

        store.recover_pretrained_csv_pair(
            self.history_file,
            self.dashboard_file,
        )

        self.assertEqual(
            self.history_file.read_bytes(),
            old_history,
        )

        self.assertEqual(
            self.dashboard_file.read_bytes(),
            old_dashboard,
        )

        self.assertFalse(
            transaction.exists()
        )

    def test_concurrent_writer_cannot_acquire_same_files(self):
        with store.locked_pretrained_csv_pair(
            self.history_file,
            self.dashboard_file,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Another KE24 output writer is active",
            ):
                self.publish()

    def test_committed_transaction_is_cleaned_without_rollback(self):
        self.publish()

        original_history = (
            self.history_file.read_bytes()
        )

        transaction = store._transaction_dir(
            self.history_file,
            self.dashboard_file,
        )

        transaction.mkdir()

        metadata = {
            "version": 1,
            "status": "COMMITTED",
            "history": str(self.history_file),
            "dashboard": str(self.dashboard_file),
            "existed": {
                "history": True,
                "dashboard": True,
            },
            "backup_sha256": {
                "history": "0" * 64,
                "dashboard": "1" * 64,
            },
        }

        store._write_state(
            transaction,
            metadata,
        )

        store.recover_pretrained_csv_pair(
            self.history_file,
            self.dashboard_file,
        )

        self.assertEqual(
            self.history_file.read_bytes(),
            original_history,
        )

        self.assertFalse(
            transaction.exists()
        )

    def test_corrupt_pending_journal_fails_closed(self):
        transaction = store._transaction_dir(
            self.history_file,
            self.dashboard_file,
        )

        transaction.mkdir()

        (
            transaction / store.STATE_FILE
        ).write_text(
            "not-json",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "manual reconciliation required",
        ):
            store.recover_pretrained_csv_pair(
                self.history_file,
                self.dashboard_file,
            )

        self.assertTrue(
            transaction.exists()
        )


if __name__ == "__main__":
    unittest.main()
