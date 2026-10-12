import unittest

import pandas as pd

from src.ml.evaluate_ke24_thresholds import (
    compare_thresholds,
)
from src.ml.classify_ke24_alerts import (
    classify_materiality,
    classify_alert_level,
    determine_classification_status,
)

TOKEN_A = "TKN_" + "a" * 32
TOKEN_B = "TKN_" + "b" * 32

class TestKE24ThresholdEvaluation(unittest.TestCase):

    def setUp(self):
        # 20 registros fictícios; os dois maiores scores são as anomalias confirmadas.
        self.dataset = pd.DataFrame({
            "anomaly_score": list(range(1, 21)),
            "ground_truth": [0] * 18 + [1, 1],
        })

        self.rules = {
            "status": "experimental_auto_classification",
            "business_thresholds_by_company": {
                TOKEN_A: {
                    "currency": "BRL",
                    "materiality_medium_min": 100.0,
                    "materiality_high_min": 300.0,
                }
            },
        }

    def test_threshold_selection_counts(self):
        comparison = compare_thresholds(self.dataset)

        selected = {
            item["threshold_percentile"]: item["selected_total"]
            for item in comparison
        }

        self.assertEqual(selected[80], 5)
        self.assertEqual(selected[90], 3)
        self.assertEqual(selected[95], 2)
        self.assertEqual(selected[99], 1)

    def test_p95_identifies_both_anomalies(self):
        comparison = compare_thresholds(
            self.dataset, thresholds=(95,)
        )

        metrics = comparison[0]

        self.assertEqual(metrics["tp"], 2)
        self.assertEqual(metrics["fp"], 0)
        self.assertEqual(metrics["fn"], 0)
        self.assertEqual(metrics["tn"], 18)

        self.assertEqual(metrics["precision"], 1.0)
        self.assertEqual(metrics["recall"], 1.0)

    def test_p99_reduces_recall(self):
        comparison = compare_thresholds(
            self.dataset, thresholds=(99,)
        )

        metrics = comparison[0]

        self.assertEqual(metrics["tp"], 1)
        self.assertEqual(metrics["fn"], 1)
        self.assertEqual(metrics["recall"], 0.5)

    def test_partial_coverage_is_reported(self):
        dataset = self.dataset.copy()

        dataset.loc[:9, "ground_truth"] = pd.NA

        comparison = compare_thresholds(
            dataset, thresholds=(95,)
        )

        metrics = comparison[0]

        self.assertEqual(metrics["labeled_records"], 10)
        self.assertEqual(
            metrics["coverage_status"], "PARTIAL_COVERAGE"
        )

    def test_reject_invalid_threshold(self):
        with self.assertRaises(ValueError):
            compare_thresholds(
                self.dataset, thresholds=(105,)
            )

    def test_missing_company_rule_keeps_alert_pending(self):
        row = {
            "company_code": TOKEN_B,
            "currency": "BRL",
            "total_absolute_value": 350.0,
        }

        level, reason = classify_materiality(
            row, self.rules
        )

        self.assertEqual(level, "PENDING_COMPANY_RULE")
        self.assertEqual(reason, "company_not_configured")

        classification_row = pd.Series({
            "anomaly_signal_level": "HIGH",
            "materiality_level": level,
            "stability_level": "HIGH",
            "review_status": "pending",
            "business_validation": pd.NA,
        })

        alert_level, _ = classify_alert_level(
            classification_row
        )

        self.assertEqual(alert_level, "PENDING")

        status = determine_classification_status(
            classification_row, self.rules
        )

        self.assertEqual(
            status, "PENDING_BUSINESS_RULE"
        )

    def test_currency_mismatch_keeps_rule_pending(self):
        row = {
            "company_code": TOKEN_A,
            "currency": "USD",
            "total_absolute_value": 350.0,
        }

        level, reason = classify_materiality(
            row, self.rules
        )

        self.assertEqual(level, "PENDING_COMPANY_RULE")
        self.assertEqual(reason, "currency_mismatch")

if __name__ == "__main__":
    unittest.main()