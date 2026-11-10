
import math
import unittest
from unittest.mock import patch

from src.pipeline.run_ke24_synthetic import load_json

from src.ml.evaluate_ke24_synthetic_holdout import (
    generate_normal_data,
    prepare_features,
)

from src.ml import prepare_ke24_training_matrix as training
from src.ml import ke24_grouped_model_runtime as runtime


class TestKE24GroupedModelRuntime(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        schema = load_json("ke24_schema.json")
        policy = load_json("ke24_privacy_policy.json")

        raw_train = generate_normal_data(
            schema, 11, "train_synthetic.xlsx"
        )

        raw_test = generate_normal_data(
            schema, 29, "test_synthetic.xlsx"
        )

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

        feature_names = train_matrix.columns[
            train_matrix.nunique(dropna=False).gt(1)
        ]

        cls.train_matrix = train_matrix[feature_names]
        cls.train_context = train_features

        cls.test_matrix = training.build_training_matrix(
            test_features, selected
        )[feature_names]

        cls.test_context = test_features

        cls.trained = runtime.train_grouped_models(
            cls.train_matrix,
            cls.train_context,
        )

    def test_two_independent_models(self):
        self.assertEqual(len(self.trained.models), 2)

        self.assertEqual(
            sorted(self.trained.group_sizes.values()),
            [20, 20],
        )

        self.assertEqual(
            len(self.trained.feature_columns),
            len(self.train_matrix.columns),
        )

        self.assertTrue(
            all(
                math.isfinite(value)
                for value in self.trained.thresholds.values()
            )
        )

    def test_all_new_records_are_scored(self):
        result = runtime.score_new_records(
            self.trained,
            self.test_matrix,
            self.test_context,
        )

        self.assertEqual(len(result), 40)

        self.assertFalse(result.isna().any().any())

        self.assertTrue(
            (
                result["reference_p95_flag"]
                == (
                    result["anomaly_score"]
                    >= result["training_p95_threshold"]
                )
            ).all()
        )

    def test_scores_are_batch_independent(self):
        full = runtime.score_new_records(
            self.trained,
            self.test_matrix,
            self.test_context,
        )

        # One record from each company/currency.
        for position in (0, 20):
            with self.subTest(position=position):
                single = runtime.score_new_records(
                    self.trained,
                    self.test_matrix.iloc[
                        [position]
                    ].reset_index(drop=True),
                    self.test_context.iloc[
                        [position]
                    ].reset_index(drop=True),
                )

                self.assertAlmostEqual(
                    float(full.iloc[position]["anomaly_score"]),
                    float(single.iloc[0]["anomaly_score"]),
                    places=12,
                )

                self.assertEqual(
                    bool(full.iloc[position]["reference_p95_flag"]),
                    bool(single.iloc[0]["reference_p95_flag"]),
                )

    def test_inference_does_not_retrain(self):
        with patch.object(
            runtime.detection,
            "train_isolation_forest",
            side_effect=AssertionError(
                "Training called during inference"
            ),
        ):
            result = runtime.score_new_records(
                self.trained,
                self.test_matrix,
                self.test_context,
            )

        self.assertEqual(len(result), 40)

    def test_feature_order_change_is_rejected(self):
        reordered = self.test_matrix.loc[
            :, list(reversed(self.test_matrix.columns))
        ]

        with self.assertRaises(ValueError):
            runtime.score_new_records(
                self.trained,
                reordered,
                self.test_context,
            )

    def test_unknown_group_is_rejected(self):
        context = self.test_context.copy()

        context.loc[0, "currency"] = "EUR"

        with self.assertRaises(ValueError):
            runtime.score_new_records(
                self.trained,
                self.test_matrix,
                context,
            )

    def test_unprotected_company_is_rejected(self):
        context = self.test_context.copy()

        context.loc[0, "company_code"] = "COMP_A"

        with self.assertRaises(ValueError):
            runtime.score_new_records(
                self.trained,
                self.test_matrix,
                context,
            )


if __name__ == "__main__":
    unittest.main()
