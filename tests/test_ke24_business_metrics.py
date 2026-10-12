import unittest

import pandas as pd

from src.ml.evaluate_ke24_business_metrics import (
    prepare_evaluation_data,
    calculate_metrics,
)

TOKEN = "TKN_" + "a" * 32

class TestKE24BusinessMetrics(unittest.TestCase):

    def setUp(self):
        self.predictions = pd.DataFrame({
            "_source_file": [TOKEN] * 4,
            "_source_row_number": [2, 3, 4, 5],
            "anomaly_score": [0.9, 0.8, 0.3, 0.2],
            "anomaly_flag": [True, True, False, False],
        })

        self.reviews = pd.DataFrame({
            "_source_file": [TOKEN] * 4,
            "_source_row_number": [2, 3, 4, 5],
            "review_status": ["validated"] * 4,
            "business_validation": [
                "TRUE_ANOMALY",
                "EXPECTED_BEHAVIOR",
                "TRUE_ANOMALY",
                "EXPECTED_BEHAVIOR",
            ],
            "validation_reason": ["Teste fictício"] * 4,
            "validated_by": ["TEST_REVIEWER"] * 4,
            "validated_at": ["2026-10-01T12:00:00Z"] * 4,
        })

    def test_confusion_matrix_and_metrics(self):
        dataset = prepare_evaluation_data(
            self.predictions, self.reviews
        )

        metrics = calculate_metrics(dataset)

        self.assertEqual(metrics["tp"], 1)
        self.assertEqual(metrics["fp"], 1)
        self.assertEqual(metrics["tn"], 1)
        self.assertEqual(metrics["fn"], 1)

        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 0.5)
        self.assertEqual(metrics["f1_score"], 0.5)

        self.assertEqual(
            metrics["coverage_status"], "FULL_COVERAGE"
        )

    def test_partial_coverage(self):
        reviews = self.reviews.iloc[:2].copy()

        dataset = prepare_evaluation_data(
            self.predictions, reviews
        )

        metrics = calculate_metrics(dataset)

        self.assertEqual(metrics["labeled_records"], 2)
        self.assertEqual(metrics["coverage_percentage"], 50.0)
        self.assertEqual(
            metrics["coverage_status"], "PARTIAL_COVERAGE"
        )

    def test_no_resolved_reviews(self):
        reviews = self.reviews.iloc[:0].copy()

        dataset = prepare_evaluation_data(
            self.predictions, reviews
        )

        metrics = calculate_metrics(dataset)

        self.assertIsNone(metrics["precision"])
        self.assertIsNone(metrics["recall"])
        self.assertIsNone(metrics["f1_score"])

    def test_needs_investigation_is_excluded(self):
        self.reviews.loc[
            0, "business_validation"
        ] = "NEEDS_INVESTIGATION"

        dataset = prepare_evaluation_data(
            self.predictions, self.reviews
        )

        metrics = calculate_metrics(dataset)

        self.assertEqual(metrics["labeled_records"], 3)
        self.assertEqual(metrics["needs_investigation"], 1)

    def test_reject_duplicate_reviews(self):
        reviews = pd.concat([
            self.reviews,
            self.reviews.iloc[[0]],
        ], ignore_index=True)

        with self.assertRaises(ValueError):
            prepare_evaluation_data(
                self.predictions, reviews
            )

    def test_reject_unprotected_identifiers(self):
        self.predictions.loc[
            0, "_source_file"
        ] = "synthetic_ke24.xlsx"

        with self.assertRaisesRegex(
            ValueError, "não protegidos"
        ):
            prepare_evaluation_data(
                self.predictions, self.reviews
            )

    def test_reject_review_without_prediction(self):
        self.reviews.loc[0, "_source_row_number"] = 999

        with self.assertRaisesRegex(
            ValueError, "sem predição correspondente"
        ):
            prepare_evaluation_data(
                self.predictions, self.reviews
            )

if __name__ == "__main__":
    unittest.main()