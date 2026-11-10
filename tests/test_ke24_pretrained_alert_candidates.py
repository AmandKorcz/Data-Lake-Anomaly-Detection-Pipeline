import unittest

import pandas as pd

from src.pipeline.run_ke24_synthetic import load_json

from src.ml.evaluate_ke24_synthetic_holdout import (
    generate_normal_data,
    prepare_features,
)

from src.ml import prepare_ke24_training_matrix as training
from src.ml import ke24_grouped_model_runtime as runtime
from src.ml import profile_ke24_anomaly_materiality as materiality

from src.ml.build_ke24_pretrained_anomaly_results import (
    build_pretrained_anomaly_results,
)

from src.ml.build_ke24_pretrained_alert_candidates import (
    build_pretrained_alert_candidates,
)


class TestKE24PretrainedAlertCandidates(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        schema = load_json("ke24_schema.json")
        policy = load_json("ke24_privacy_policy.json")

        raw_train = pd.concat(
            [
                generate_normal_data(
                    schema,
                    seed,
                    f"train_{seed}.xlsx",
                )
                for seed in range(11, 21)
            ],
            ignore_index=True,
        )

        raw_test = generate_normal_data(
            schema,
            29,
            "new_documents.xlsx",
        )

        train_features, manifest = prepare_features(
            raw_train,
            schema,
            policy,
        )

        test_features, _ = prepare_features(
            raw_test,
            schema,
            policy,
        )

        selected = training.get_selected_features(
            manifest
        )

        train_matrix = training.build_training_matrix(
            train_features,
            selected,
        )

        feature_columns = train_matrix.columns[
            train_matrix.nunique(dropna=False).gt(1)
        ]

        trained = runtime.train_grouped_models(
            train_matrix[feature_columns],
            train_features,
        )

        test_matrix = training.build_training_matrix(
            test_features,
            selected,
        )[feature_columns]

        model_manifest = {
            "model_version": "synthetic-16e2a",
            "threshold_method": "training_p95",
            "feature_columns": list(
                trained.feature_columns
            ),
            "group_count": len(trained.models),
        }

        anomaly_results = (
            build_pretrained_anomaly_results(
                trained,
                test_matrix,
                test_features,
                model_manifest=model_manifest,
            )
        )

        financial = materiality.get_financial_features(
            manifest
        )

        cls.profiled = (
            materiality.build_materiality_dataset(
                test_features,
                anomaly_results,
                financial,
            )
        )

    def test_candidates_follow_training_p95(self):
        candidates = build_pretrained_alert_candidates(
            self.profiled
        )

        expected = self.profiled[
            self.profiled["reference_p95_flag"]
        ]

        self.assertEqual(
            len(candidates),
            len(expected),
        )

        self.assertTrue(
            candidates["reference_p95_flag"].all()
        )

        self.assertTrue(
            candidates["score_margin"].ge(0).all()
        )

    def test_no_batch_top_percentile_fallback(self):
        modified = self.profiled.copy()

        # Simulate a batch with no scores above P95.
        modified["training_p95_threshold"] = (
            modified["anomaly_score"].max() + 1
        )

        modified["anomaly_flag"] = False
        modified["reference_p95_flag"] = False

        candidates = build_pretrained_alert_candidates(
            modified
        )

        self.assertTrue(candidates.empty)

    
    def test_selection_is_order_independent(self):
        original = build_pretrained_alert_candidates(
            self.profiled
        )

        shuffled = self.profiled.sample(
            frac=1,
            random_state=42,
        ).reset_index(drop=True)

        reordered = build_pretrained_alert_candidates(
            shuffled
        )

        identity = [
            "_source_file_sha256",
            "_source_row_number",
            "company_code",
        ]

        self.assertEqual(
            set(map(tuple, original[identity].to_numpy())),
            set(map(tuple, reordered[identity].to_numpy())),
        )

    def test_inconsistent_flag_is_rejected(self):
        modified = self.profiled.copy()

        modified.loc[0, "anomaly_flag"] = (
            not bool(modified.loc[0, "anomaly_flag"])
        )

        with self.assertRaisesRegex(
            ValueError,
            "Inconsistent operational anomaly flags",
        ):
            build_pretrained_alert_candidates(modified)

    def test_inconsistent_threshold_is_rejected(self):
        modified = self.profiled.copy()

        modified.loc[0, "training_p95_threshold"] = (
            float(modified.loc[0, "anomaly_score"])
            + 100
        )

        modified.loc[0, "reference_p95_flag"] = True
        modified.loc[0, "anomaly_flag"] = True

        with self.assertRaisesRegex(
            ValueError,
            "Inconsistent training P95 flags",
        ):
            build_pretrained_alert_candidates(modified)

    def test_mixed_versions_are_rejected(self):
        modified = self.profiled.copy()

        modified.loc[0, "model_version"] = (
            "synthetic-other"
        )

        with self.assertRaisesRegex(
            ValueError,
            "Mixed or missing model versions",
        ):
            build_pretrained_alert_candidates(modified)

    def test_duplicate_records_are_rejected(self):
        modified = self.profiled.copy()

        for column in (
            "_source_file_sha256",
            "_source_row_number",
            "company_code",
        ):
            modified.loc[1, column] = (
                modified.loc[0, column]
            )

        with self.assertRaisesRegex(
            ValueError,
            "Duplicate record identities",
        ):
            build_pretrained_alert_candidates(modified)


if __name__ == "__main__":
    unittest.main()
