import unittest

import pandas as pd

from src.ml.ke24_record_identity import (
    validate_record_identity,
)

from src.ml.build_ke24_pretrained_alert_candidates import (
    build_pretrained_alert_candidates,
    attach_pretrained_stability,
)


def token(character):
    return "TKN_" + character * 32


def make_materiality():
    return pd.DataFrame({
        "_source_file": [token("d"), token("d")],
        "_source_file_sha256": [
            token("a"),
            token("b"),
        ],
        "_source_row_number": [2, 2],
        "company_code": [token("c"), token("c")],
        "currency": ["BRL", "BRL"],
        "currency_type": [10, 10],
        "anomaly_score": [0.62, 0.67],
        "anomaly_rank": [1, 2],
        "anomaly_flag": [True, True],
        "reference_p95_flag": [True, True],
        "training_p95_threshold": [0.55, 0.55],
        "model_version": [
            "synthetic-identity-v1",
            "synthetic-identity-v1",
        ],
        "signal_method": [
            "training_p95",
            "training_p95",
        ],
        "materiality_percentile": [50.0, 100.0],
        "total_absolute_value": [500.0, 700.0],
    })


def make_stability():
    result = make_materiality()[[
        "_source_file_sha256",
        "_source_row_number",
        "company_code",
        "currency",
        "currency_type",
    ]].copy()

    result["mean_anomaly_score"] = [0.60, 0.65]
    result["score_std"] = [0.01, 0.01]
    result["mean_rank"] = [1.0, 2.0]
    result["score_seed_42"] = [0.62, 0.67]

    votes = {
        7: [True, True],
        21: [True, True],
        42: [True, True],
        84: [True, False],
        123: [True, False],
    }

    for seed, values in votes.items():
        result[f"flag_seed_{seed}"] = values

    result["flag_count"] = [5, 3]
    result["stability_rate"] = [100.0, 60.0]

    return result


class TestKE24RecordIdentity(unittest.TestCase):

    def setUp(self):
        self.materiality = make_materiality()
        self.stability = make_stability()

    def test_same_filename_and_row_are_distinct(self):
        self.assertTrue(
            validate_record_identity(self.materiality)
        )

        self.assertEqual(
            self.materiality["_source_file"].nunique(),
            1,
        )

        self.assertEqual(
            self.materiality[
                "_source_row_number"
            ].nunique(),
            1,
        )

        self.assertEqual(
            self.materiality[
                "_source_file_sha256"
            ].nunique(),
            2,
        )

    def test_candidates_keep_both_sources(self):
        candidates = build_pretrained_alert_candidates(
            self.materiality
        )

        self.assertEqual(len(candidates), 2)

        self.assertEqual(
            candidates[
                "_source_file_sha256"
            ].nunique(),
            2,
        )

    def test_stability_joins_by_source_hash(self):
        candidates = build_pretrained_alert_candidates(
            self.materiality
        )

        reversed_stability = (
            self.stability.iloc[::-1]
            .reset_index(drop=True)
        )

        result = attach_pretrained_stability(
            candidates,
            reversed_stability,
        )

        rates = result.set_index(
            "_source_file_sha256"
        )["stability_rate"].to_dict()

        self.assertEqual(rates[token("a")], 100.0)
        self.assertEqual(rates[token("b")], 60.0)

    def test_true_duplicate_is_rejected(self):
        modified = self.materiality.copy()

        modified.loc[
            1, "_source_file_sha256"
        ] = token("a")

        with self.assertRaisesRegex(
            ValueError,
            "Duplicate record identities",
        ):
            validate_record_identity(modified)

    def test_unprotected_hash_is_rejected(self):
        modified = self.materiality.copy()

        modified.loc[
            0, "_source_file_sha256"
        ] = "raw_hash"

        with self.assertRaisesRegex(
            ValueError,
            "Invalid protected source identity",
        ):
            validate_record_identity(modified)

    def test_wrong_stability_hash_is_rejected(self):
        candidates = build_pretrained_alert_candidates(
            self.materiality
        )

        modified = self.stability.copy()

        modified.loc[
            0, "_source_file_sha256"
        ] = token("e")

        with self.assertRaisesRegex(
            ValueError,
            "Stability row not found",
        ):
            attach_pretrained_stability(
                candidates,
                modified,
            )


if __name__ == "__main__":
    unittest.main()
