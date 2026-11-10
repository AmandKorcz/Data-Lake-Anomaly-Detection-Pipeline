
import unittest

import pandas as pd

from src.ml.classify_ke24_pretrained_alerts import (
    build_pretrained_classification,
)


COMP_A = "TKN_" + "a" * 32
COMP_B = "TKN_" + "b" * 32


def make_candidates():
    return pd.DataFrame({
        "_source_file": [
            "TKN_" + "c" * 32,
            "TKN_" + "c" * 32,
        ],
        "_source_row_number": [2, 3],
        "company_code": [COMP_A, COMP_B],
        "currency": ["BRL", "USD"],
        "currency_type": [10, 10],
        "reference_document": [
            "TKN_" + "d" * 32,
            "TKN_" + "e" * 32,
        ],
        "anomaly_score": [0.62, 0.54],
        "training_p95_threshold": [0.55, 0.52],
        "score_margin": [0.07, 0.02],
        "anomaly_rank": [1, 1],
        "anomaly_flag": [True, True],
        "reference_p95_flag": [True, True],
        "model_version": [
            "synthetic-test-v1",
            "synthetic-test-v1",
        ],
        "signal_method": [
            "training_p95",
            "training_p95",
        ],
        "materiality_percentile": [100.0, 80.0],
        "total_absolute_value": [500.0, 150.0],
        "stability_rate": [80.0, 20.0],
    })


def make_rules():
    return {
        "status": "experimental_auto_classification",
        "currency_basis": {
            "currency_type": 10,
        },
        "business_thresholds_by_company": {
            COMP_A: {
                "currency": "BRL",
                "materiality_medium_min": 100.0,
                "materiality_high_min": 300.0,
            },
            COMP_B: {
                "currency": "USD",
                "materiality_medium_min": 100.0,
                "materiality_high_min": 300.0,
            },
        },
        "technical_thresholds": {
            # Fictional thresholds for unit tests only.
            "pretrained_margin_medium_min": 0.0,
            "pretrained_margin_high_min": 0.05,
            "stability_medium_min": 40,
            "stability_high_min": 100,
        },
    }


class TestKE24PretrainedClassification(unittest.TestCase):

    def setUp(self):
        self.candidates = make_candidates()
        self.rules = make_rules()

    def test_financial_levels_are_classified(self):
        result = build_pretrained_classification(
            self.candidates,
            self.rules,
        )

        self.assertEqual(
            result["alert_level"].tolist(),
            ["HIGH", "LOW"],
        )

        self.assertEqual(
            result["anomaly_signal_level"].tolist(),
            ["HIGH", "MEDIUM"],
        )

    def test_historical_p95_is_preserved(self):
        result = build_pretrained_classification(
            self.candidates,
            self.rules,
        )

        self.assertEqual(
            result["score_margin"].tolist(),
            self.candidates["score_margin"].tolist(),
        )

        self.assertNotIn(
            "anomaly_score_percentile",
            result.columns,
        )

    def test_missing_thresholds_remain_pending(self):
        rules = make_rules()

        del rules["technical_thresholds"][
            "pretrained_margin_high_min"
        ]

        result = build_pretrained_classification(
            self.candidates,
            rules,
        )

        self.assertTrue(
            result["anomaly_signal_level"].eq(
                "PENDING_TECHNICAL_RULE"
            ).all()
        )

        self.assertTrue(
            result["classification_status"].eq(
                "PENDING_TECHNICAL_RULE"
            ).all()
        )

    def test_business_review_starts_pending(self):
        rules = make_rules()
        rules["status"] = "business_review_required"

        result = build_pretrained_classification(
            self.candidates,
            rules,
        )

        self.assertTrue(
            result["review_status"].eq("pending").all()
        )

        self.assertTrue(
            result["classification_status"].eq(
                "PENDING_BUSINESS_VALIDATION"
            ).all()
        )

    def test_invalid_margin_is_rejected(self):
        modified = self.candidates.copy()
        modified.loc[0, "score_margin"] = 5.0

        with self.assertRaisesRegex(
            ValueError,
            "Inconsistent score margin",
        ):
            build_pretrained_classification(
                modified,
                self.rules,
            )

    def test_empty_candidates_are_supported(self):
        empty = self.candidates.iloc[0:0].copy()

        result = build_pretrained_classification(
            empty,
            self.rules,
        )

        self.assertTrue(result.empty)
        self.assertIn("alert_level", result.columns)
        self.assertIn(
            "classification_status",
            result.columns,
        )

    def test_currency_mismatch_is_rejected(self):
        modified = self.candidates.copy()
        modified.loc[0, "currency_type"] = 20

        with self.assertRaises(ValueError):
            build_pretrained_classification(
                modified,
                self.rules,
            )


if __name__ == "__main__":
    unittest.main()
