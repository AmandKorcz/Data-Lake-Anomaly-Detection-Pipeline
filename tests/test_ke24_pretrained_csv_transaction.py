import os
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.alerts import (
    ke24_pretrained_csv_transaction as store,
)


class TestKE24PretrainedCSVTransaction(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        folder = Path(self.temp.name)

        self.history_file = folder / "history.csv"
        self.dashboard_file = folder / "dashboard.csv"

        self.history = pd.DataFrame({
            "alert_id": ["ALERT_1"],
            "investigation_status": ["PENDING"],
        })

        self.dashboard = pd.DataFrame({
            "alert_id": ["ALERT_1"],
            "alert_count": [1],
        })

    def publish(self, history=None, dashboard=None):
        store.publish_pretrained_csv_pair(
            self.history if history is None else history,
            self.dashboard if dashboard is None else dashboard,
            self.history_file,
            self.dashboard_file,
        )

    def fail_dashboard_replace(self):
        original_replace = os.replace

        def replace_with_failure(source, destination):
            if Path(destination) == self.dashboard_file:
                raise OSError(
                    "Simulated dashboard write failure"
                )

            return original_replace(
                source,
                destination,
            )

        return replace_with_failure

    def test_both_files_are_published(self):
        self.publish()

        self.assertTrue(self.history_file.exists())
        self.assertTrue(self.dashboard_file.exists())

        history = pd.read_csv(self.history_file)
        dashboard = pd.read_csv(self.dashboard_file)

        self.assertEqual(len(history), 1)
        self.assertEqual(len(dashboard), 1)

    def test_existing_files_are_restored_on_failure(self):
        self.publish()

        original_history = self.history_file.read_bytes()
        original_dashboard = self.dashboard_file.read_bytes()

        updated = self.history.copy()
        updated["investigation_status"] = "INVESTIGATING"

        with patch.object(
            store.os,
            "replace",
            side_effect=self.fail_dashboard_replace(),
        ):
            with self.assertRaises(OSError):
                self.publish(history=updated)

        self.assertEqual(
            self.history_file.read_bytes(),
            original_history,
        )

        self.assertEqual(
            self.dashboard_file.read_bytes(),
            original_dashboard,
        )

    def test_first_execution_failure_leaves_no_files(self):
        with patch.object(
            store.os,
            "replace",
            side_effect=self.fail_dashboard_replace(),
        ):
            with self.assertRaises(OSError):
                self.publish()

        self.assertFalse(self.history_file.exists())
        self.assertFalse(self.dashboard_file.exists())

    def test_staging_failure_preserves_existing_files(self):
        self.publish()

        original_history = self.history_file.read_bytes()
        original_dashboard = self.dashboard_file.read_bytes()

        original_stage = store._stage_csv
        calls = [0]

        def fail_second_stage(dataset, destination):
            calls[0] += 1

            if calls[0] == 2:
                raise OSError(
                    "Simulated staging failure"
                )

            return original_stage(
                dataset,
                destination,
            )

        with patch.object(
            store,
            "_stage_csv",
            side_effect=fail_second_stage,
        ):
            with self.assertRaises(OSError):
                self.publish()

        self.assertEqual(
            self.history_file.read_bytes(),
            original_history,
        )

        self.assertEqual(
            self.dashboard_file.read_bytes(),
            original_dashboard,
        )

        self.assertEqual(
            list(
                Path(self.temp.name).glob(
                    ".ke24_*"
                )
            ),
            [],
        )

    def test_same_destination_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "must differ",
        ):
            store.publish_pretrained_csv_pair(
                self.history,
                self.dashboard,
                self.history_file,
                self.history_file,
            )


if __name__ == "__main__":
    unittest.main()
