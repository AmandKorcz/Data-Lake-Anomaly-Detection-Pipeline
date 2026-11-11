
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.pipeline import run_ke24_synthetic as pipeline
from src.alerts.validate_ke24_dashboard_export import (
    validate_dashboard_export,
)


class TestKE24GroupedPipelineIntegration(unittest.TestCase):

    def test_grouped_pipeline_preserves_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            history_file = root / "history.csv"
            dashboard_file = root / "dashboard.csv"

            with (
                patch.object(
                    pipeline, "HISTORY_FILE", history_file
                ),
                patch.object(
                    pipeline, "OUTPUT_FILE", dashboard_file
                ),
            ):
                # First execution
                pipeline.run_pipeline()

                history_before = pd.read_csv(history_file)
                dashboard_before = pd.read_csv(dashboard_file)

                self.assertGreater(len(history_before), 0)

                self.assertEqual(
                    set(dashboard_before["currency"]),
                    {"BRL", "USD"},
                )

                self.assertEqual(
                    history_before.groupby(
                        ["company_code", "currency"]
                    ).ngroups,
                    2,
                )

                validate_dashboard_export(dashboard_before)

                print("TEST 1: Grouped export valid - OK")

                # Simulate analyst investigation
                alert_id = history_before.loc[0, "alert_id"]

                first_dates = (
                    history_before
                    .set_index("alert_id")["detected_at_utc"]
                    .to_dict()
                )

                history_before.loc[
                    history_before["alert_id"].eq(alert_id),
                    "investigation_status",
                ] = "INVESTIGATING"

                history_before.to_csv(
                    history_file,
                    index=False,
                    encoding="utf-8-sig",
                )

                # Second execution
                pipeline.run_pipeline()

            history_after = pd.read_csv(history_file)
            dashboard_after = pd.read_csv(dashboard_file)

            self.assertFalse(
                history_after["alert_id"].duplicated().any()
            )

            self.assertEqual(
                len(history_before),
                len(history_after),
            )

            self.assertEqual(
                set(history_before["alert_id"]),
                set(history_after["alert_id"]),
            )

            print("TEST 2: No duplicate alerts - OK")

            updated = history_after.set_index("alert_id")

            self.assertEqual(
                updated.loc[alert_id, "investigation_status"],
                "INVESTIGATING",
            )

            print("TEST 3: Investigation preserved - OK")

            self.assertEqual(
                updated["detected_at_utc"].to_dict(),
                first_dates,
            )

            print("TEST 4: First detection preserved - OK")

            dashboard_record = (
                dashboard_after
                .set_index("alert_id")
                .loc[alert_id]
            )

            self.assertEqual(
                dashboard_record["investigation_status"],
                "INVESTIGATING",
            )

            validate_dashboard_export(dashboard_after)

            print("TEST 5: Dashboard updated - OK")
            print("GROUPED HISTORY INTEGRATION PASSED")


if __name__ == "__main__":
    unittest.main()
