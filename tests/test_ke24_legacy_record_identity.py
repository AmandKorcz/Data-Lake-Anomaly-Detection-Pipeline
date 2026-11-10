import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.ml.ke24_record_identity import (
    IDENTITY_COLUMNS,
)

from src.ml import build_ke24_alert_candidates as candidates
from src.ml import build_ke24_business_validation_dataset as business
from src.ml import evaluate_ke24_isolation_forest_stability as stability


def token(char):
    return "TKN_" + char * 32


def make_datasets():
    rows = []

    for source_hash, company, currency in (
        (token("a"), token("c"), "BRL"),
        (token("b"), token("d"), "USD"),
    ):
        for index in range(20):
            rows.append({
                "_source_file": token("e"),
                "_source_file_sha256": source_hash,
                "_source_row_number": index + 2,
                "company_code": company,
                "currency": currency,
                "currency_type": 10,
                "period_key": "2026-03",
                "profit_center": token("f"),
                "product": token("1"),
                "reference_document": token("2"),
                "anomaly_rank": 20 - index,
                "anomaly_flag": index >= 18,
                "anomaly_score": 0.3 + index * 0.01,
                "total_absolute_value": float(500 + index),
                "materiality_percentile": (index + 1) * 5.0,
                "dominant_measure": "revenue",
                "dominant_measure_value": float(500 + index),
            })

    materiality = pd.DataFrame(rows)

    stable = materiality[
        IDENTITY_COLUMNS
    ].copy()

    stable["mean_anomaly_score"] = (
        materiality["anomaly_score"].to_numpy()
    )
    stable["score_std"] = 0.01
    stable["flag_count"] = 3
    stable["mean_rank"] = (
        materiality["anomaly_rank"].to_numpy()
    )

    return materiality, stable


class TestKE24LegacyRecordIdentity(unittest.TestCase):

    def setUp(self):
        self.materiality, self.stability = make_datasets()

    def test_same_filename_and_row_are_accepted(self):
        candidates.validate_inputs(
            self.materiality,
            self.stability,
        )

        result = candidates.build_alert_candidates(
            self.materiality,
            self.stability,
        )

        self.assertEqual(len(result), 4)

        top_rows = result.loc[
            result["_source_row_number"].eq(21)
        ]

        self.assertEqual(
            set(top_rows["_source_file_sha256"]),
            {token("a"), token("b")},
        )

    def test_business_validation_uses_full_identity(self):
        selected = candidates.build_alert_candidates(
            self.materiality,
            self.stability,
        )

        ml_features = self.materiality[
            IDENTITY_COLUMNS + [
                "currency",
                "currency_type",
            ]
        ].copy()

        with patch.object(
            business,
            "load_existing_validation",
            return_value=None,
        ):
            result = business.build_validation_dataset(
                selected,
                ml_features,
            )

        self.assertEqual(len(result), 4)
        self.assertEqual(
            result["_source_file_sha256"].nunique(),
            2,
        )
        self.assertTrue(
            result["review_status"].eq("pending").all()
        )

    def test_review_does_not_cross_sources(self):
        selected = candidates.build_alert_candidates(
            self.materiality,
            self.stability,
        )

        ml_features = self.materiality[
            IDENTITY_COLUMNS + [
                "currency",
                "currency_type",
            ]
        ].copy()

        review = selected.loc[
            selected["_source_file_sha256"].eq(token("a"))
            & selected["_source_row_number"].eq(21),
            IDENTITY_COLUMNS,
        ].copy()

        self.assertEqual(len(review), 1)

        review["review_status"] = "validated"
        review["business_validation"] = "TRUE_ANOMALY"
        review["validation_reason"] = "Synthetic review"
        review["validated_by"] = "Synthetic reviewer"
        review["validated_at"] = "2026-03-10T12:00:00Z"
        review["business_notes"] = "Synthetic case"

        with patch.object(
            business,
            "load_existing_validation",
            return_value=review,
        ):
            result = business.build_validation_dataset(
                selected,
                ml_features,
            )

        validated = result.loc[
            result["review_status"].eq("validated")
        ]

        self.assertEqual(len(validated), 1)
        self.assertEqual(
            validated.iloc[0]["_source_file_sha256"],
            token("a"),
        )
        self.assertEqual(
            int(validated.iloc[0]["_source_row_number"]),
            21,
        )

    def test_legacy_stability_preserves_source_hash(self):
        context = self.materiality.copy()

        scores = pd.DataFrame({
            "anomaly_score": context[
                "anomaly_score"
            ].to_numpy(),
            "anomaly_flag": context[
                "anomaly_flag"
            ].to_numpy(),
            "anomaly_rank": context[
                "anomaly_rank"
            ].to_numpy(),
        })

        matrix = pd.DataFrame({
            "feature": range(len(context)),
        })

        with patch.object(
            stability,
            "build_grouped_anomaly_scores",
            return_value=scores,
        ):
            result = stability.build_grouped_stability(
                matrix,
                context,
            )

        self.assertEqual(len(result), 40)
        self.assertIn(
            "_source_file_sha256",
            result.columns,
        )
        self.assertEqual(
            result["_source_file_sha256"].nunique(),
            2,
        )

    def test_old_review_contract_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "review.csv"

            old = pd.DataFrame({
                "_source_file": [token("e")],
                "_source_row_number": [21],
                "review_status": ["pending"],
            })

            old.to_csv(path, index=False)

            with patch.object(
                business,
                "OUTPUT_FILE",
                path,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "outdated or incomplete",
                ):
                    business.load_existing_validation()


if __name__ == "__main__":
    unittest.main()
