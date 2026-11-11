import unittest
from datetime import datetime, timezone

import pandas as pd

from src.pipeline.run_ke24_synthetic import load_json

from src.alerts.build_ke24_pretrained_outputs import (
    build_pretrained_outputs,
)


def token(char):
    return "TKN_" + char * 32


def make_classification():
    return pd.DataFrame({
        "_source_file": [token("a")],
        "_source_file_sha256": [token("b")],
        "_source_row_number": [2],
        "company_code": [token("c")],
        "reference_document": [token("d")],
        "period_key": ["2026-03"],
        "profit_center": [token("e")],
        "product": [token("f")],
        "currency": ["BRL"],
        "currency_type": [10],
        "alert_level": ["HIGH"],
        "alert_reason": ["synthetic_test"],
        "classification_status": [
            "EXPERIMENTAL_CLASSIFICATION"
        ],
        "anomaly_score": [0.62],
        "anomaly_rank": [1],
        "anomaly_flag": [True],
        "training_p95_threshold": [0.55],
        "score_margin": [0.07],
        "reference_p95_flag": [True],
        "model_version": ["synthetic-v1"],
        "signal_method": ["training_p95"],
        "total_absolute_value": [500.0],
        "materiality_percentile": [100.0],
        "stability_rate": [80.0],
        "dominant_measure": ["revenue"],
        "dominant_measure_value": [500.0],
    })


class TestKE24PretrainedOutputs(unittest.TestCase):

    def setUp(self):
        self.policy = load_json(
            "ke24_privacy_policy.json"
        )

        self.classified = make_classification()

        self.first_date = datetime(
            2026, 3, 10,
            tzinfo=timezone.utc,
        )

        self.second_date = datetime(
            2026, 3, 11,
            tzinfo=timezone.utc,
        )

    def build(self, classified=None, previous=None, date=None):
        return build_pretrained_outputs(
            self.classified
            if classified is None
            else classified,
            previous,
            self.first_date if date is None else date,
            self.policy,
        )

    def test_history_and_dashboard_are_created(self):
        history, dashboard = self.build()

        self.assertEqual(len(history), 1)
        self.assertEqual(len(dashboard), 1)

        self.assertEqual(
            dashboard.loc[0, "model_version"],
            "synthetic-v1",
        )

        self.assertEqual(
            int(dashboard.loc[0, "alert_count"]),
            1,
        )

    def test_historical_p95_is_not_fabricated(self):
        _, dashboard = self.build()

        self.assertNotIn(
            "anomaly_score_percentile",
            dashboard.columns,
        )

        self.assertAlmostEqual(
            float(dashboard.loc[0, "score_margin"]),
            0.07,
        )

        self.assertAlmostEqual(
            float(
                dashboard.loc[
                    0, "training_p95_threshold"
                ]
            ),
            0.55,
        )

    def test_investigation_and_first_date_are_preserved(self):
        previous, _ = self.build()

        original_id = previous.loc[0, "alert_id"]
        original_date = previous.loc[
            0, "detected_at_utc"
        ]

        previous.loc[
            0, "investigation_status"
        ] = "INVESTIGATING"

        history, dashboard = self.build(
            previous=previous,
            date=self.second_date,
        )

        self.assertEqual(len(history), 1)

        self.assertEqual(
            history.loc[0, "alert_id"],
            original_id,
        )

        self.assertEqual(
            history.loc[0, "detected_at_utc"],
            original_date,
        )

        self.assertEqual(
            dashboard.loc[0, "investigation_status"],
            "INVESTIGATING",
        )

    def test_empty_initial_batch_is_valid(self):
        empty = self.classified.iloc[0:0].copy()

        history, dashboard = self.build(
            classified=empty,
        )

        self.assertTrue(history.empty)
        self.assertTrue(dashboard.empty)

        self.assertIn("alert_count", dashboard.columns)

    def test_empty_batch_preserves_previous_history(self):
        previous, _ = self.build()

        previous.loc[
            0, "investigation_status"
        ] = "INVESTIGATING"

        empty = self.classified.iloc[0:0].copy()

        history, dashboard = self.build(
            classified=empty,
            previous=previous,
            date=self.second_date,
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(len(dashboard), 1)

        self.assertEqual(
            history.loc[0, "investigation_status"],
            "INVESTIGATING",
        )

    def test_unprotected_identifier_is_rejected(self):
        modified = self.classified.copy()

        modified.loc[
            0, "reference_document"
        ] = "DOC_REAL_001"

        with self.assertRaises(ValueError):
            self.build(classified=modified)

    def test_inconsistent_score_margin_is_rejected(self):
        modified = self.classified.copy()

        modified.loc[0, "score_margin"] = 2.0

        with self.assertRaisesRegex(
            ValueError,
            "Inconsistent score margin",
        ):
            self.build(classified=modified)

    def test_legacy_history_cannot_be_mixed(self):
        previous, _ = self.build()

        previous[
            "anomaly_score_percentile"
        ] = 100.0

        with self.assertRaisesRegex(
            ValueError,
            "Legacy percentile history",
        ):
            self.build(previous=previous)


if __name__ == "__main__":
    unittest.main()
