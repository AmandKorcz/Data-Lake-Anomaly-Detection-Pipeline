import unittest

import numpy as np
import pandas as pd

from src.pipeline.run_ke24_synthetic import load_json

from src.ml.evaluate_ke24_synthetic_holdout import (
    generate_normal_data,
    prepare_features,
)

from src.ml import prepare_ke24_training_matrix as training
from src.ml import profile_ke24_anomaly_materiality as materiality
from src.ml import ke24_grouped_model_runtime as runtime

from src.ml.evaluate_ke24_isolation_forest_stability import (
    RANDOM_STATES,
)

from src.ml.build_ke24_pretrained_anomaly_results import (
    build_pretrained_anomaly_results,
)

from src.ml.build_ke24_pretrained_alert_candidates import (
    build_pretrained_alert_candidates,
    attach_pretrained_stability,
)

from src.ml.build_ke24_pretrained_stability import (
    build_pretrained_stability,
)


class TestKE24PretrainedCandidatesStability(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        schema = load_json("ke24_schema.json")
        policy = load_json("ke24_privacy_policy.json")

        raw_train = generate_normal_data(
            schema, 11, "train.xlsx"
        )

        raw_test = generate_normal_data(
            schema, 29, "test.xlsx"
        )

        # Synthetic extreme anomalies for integration.
        raw_test.loc[2, "revenue"] = 80000.0
        raw_test.loc[22, "revenue"] = 200000.0

        train_features, manifest = prepare_features(
            raw_train, schema, policy
        )

        test_features, _ = prepare_features(
            raw_test, schema, policy
        )

        selected = training.get_selected_features(
            manifest
        )

        train_matrix = training.build_training_matrix(
            train_features, selected
        )

        feature_columns = train_matrix.columns[
            train_matrix.nunique(dropna=False).gt(1)
        ]

        train_matrix = train_matrix[feature_columns]

        test_matrix = training.build_training_matrix(
            test_features, selected
        )[feature_columns]

        # Models are fitted before inference.
        models = {
            seed: runtime.train_grouped_models(
                train_matrix,
                train_features,
                random_state=seed,
            )
            for seed in RANDOM_STATES
        }

        main_model = models[42]

        model_manifest = {
            "model_version": "synthetic-16e2c",
            "threshold_method": "training_p95",
            "feature_columns": list(
                main_model.feature_columns
            ),
            "group_count": len(main_model.models),
        }

        anomaly_results = build_pretrained_anomaly_results(
            main_model,
            test_matrix,
            test_features,
            model_manifest=model_manifest,
        )

        financial = materiality.get_financial_features(
            manifest
        )

        profiled = materiality.build_materiality_dataset(
            test_features,
            anomaly_results,
            financial,
        )

        cls.candidates = build_pretrained_alert_candidates(
            profiled
        )

        cls.stability = build_pretrained_stability(
            models,
            test_matrix,
            test_features,
        )

        if cls.candidates.empty:
            raise AssertionError(
                "Synthetic test expected candidates"
            )

    def merge(self, candidates=None, stability=None):
        return attach_pretrained_stability(
            self.candidates if candidates is None
            else candidates,
            self.stability if stability is None
            else stability,
        )

    def matching_index(self, stability):
        candidate = self.candidates.iloc[0]

        mask = (
            stability["_source_file"].eq(
                candidate["_source_file"]
            )
            &
            stability["_source_row_number"].eq(
                candidate["_source_row_number"]
            )
        )

        return stability.index[mask][0]

    def test_candidates_receive_stability(self):
        result = self.merge()

        self.assertEqual(
            len(result),
            len(self.candidates),
        )

        self.assertTrue(
            result["stability_rate"]
            .between(0, 100)
            .all()
        )

        self.assertFalse(
            result["mean_anomaly_score"].isna().any()
        )

    def test_primary_model_matches_seed_42(self):
        result = self.merge()

        self.assertTrue(np.allclose(
            result["anomaly_score"],
            result["score_seed_42"],
            rtol=0,
            atol=1e-12,
        ))

        self.assertTrue(
            result["reference_p95_flag"]
            .eq(result["flag_seed_42"])
            .all()
        )

    def test_missing_stability_record_is_rejected(self):
        modified = self.stability.copy()

        position = self.matching_index(modified)
        modified = modified.drop(index=position)

        with self.assertRaisesRegex(
            ValueError,
            "Stability row not found",
        ):
            self.merge(stability=modified)

    def test_inconsistent_seed_42_is_rejected(self):
        modified = self.stability.copy()

        position = self.matching_index(modified)
        modified.loc[
            position, "score_seed_42"
        ] += 0.1

        with self.assertRaisesRegex(
            ValueError,
            "Primary model differs",
        ):
            self.merge(stability=modified)

    def test_zero_candidates_is_valid(self):
        empty = self.candidates.iloc[0:0].copy()

        result = self.merge(candidates=empty)

        self.assertTrue(result.empty)
        self.assertIn("stability_rate", result.columns)

    def test_invalid_stability_rate_is_rejected(self):
        modified = self.stability.copy()

        position = self.matching_index(modified)
        modified.loc[
            position, "stability_rate"
        ] = 55.0

        with self.assertRaisesRegex(
            ValueError,
            "Inconsistent stability rate",
        ):
            self.merge(stability=modified)

    def test_currency_mismatch_is_rejected(self):
        modified = self.stability.copy()

        position = self.matching_index(modified)
        modified.loc[position, "currency"] = "EUR"

        with self.assertRaisesRegex(
            ValueError,
            "Inconsistent stability context",
        ):
            self.merge(stability=modified)


if __name__ == "__main__":
    unittest.main()
