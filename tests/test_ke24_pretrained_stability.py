import unittest
from dataclasses import replace
from unittest.mock import patch

from src.pipeline.run_ke24_synthetic import load_json

from src.ml.evaluate_ke24_synthetic_holdout import (
    generate_normal_data,
    prepare_features,
)

from src.ml import prepare_ke24_training_matrix as training
from src.ml import ke24_grouped_model_runtime as runtime

from src.ml.evaluate_ke24_isolation_forest_stability import (
    RANDOM_STATES,
)

from src.ml.build_ke24_pretrained_stability import (
    build_pretrained_stability,
)


class TestKE24PretrainedStability(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        schema = load_json("ke24_schema.json")
        policy = load_json("ke24_privacy_policy.json")

        train_raw = generate_normal_data(
            schema,
            11,
            "synthetic_train.xlsx",
        )

        test_raw = generate_normal_data(
            schema,
            29,
            "synthetic_test.xlsx",
        )

        train_features, manifest = prepare_features(
            train_raw,
            schema,
            policy,
        )

        test_features, _ = prepare_features(
            test_raw,
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

        cls.train_matrix = train_matrix[
            feature_columns
        ]

        cls.train_context = train_features

        cls.test_matrix = training.build_training_matrix(
            test_features,
            selected,
        )[feature_columns]

        cls.test_context = test_features

        # This happens during the training phase only.
        cls.models_by_seed = {
            seed: runtime.train_grouped_models(
                cls.train_matrix,
                cls.train_context,
                random_state=seed,
            )
            for seed in RANDOM_STATES
        }

    def build_result(self):
        return build_pretrained_stability(
            self.models_by_seed,
            self.test_matrix,
            self.test_context,
        )

    def test_five_models_score_all_records(self):
        result = self.build_result()

        self.assertEqual(len(RANDOM_STATES), 5)
        self.assertEqual(len(result), 40)

        self.assertTrue(
            result["flag_count"].between(0, 5).all()
        )

        self.assertTrue(
            result["stability_rate"].between(
                0, 100
            ).all()
        )

    def test_stability_matches_model_votes(self):
        result = self.build_result()

        flag_columns = [
            f"flag_seed_{seed}"
            for seed in RANDOM_STATES
        ]

        expected = (
            result[flag_columns].sum(axis=1)
        )

        self.assertTrue(
            result["flag_count"].eq(expected).all()
        )

        self.assertTrue(
            result["stability_rate"].eq(
                expected / 5 * 100
            ).all()
        )

    def test_inference_never_retrains(self):
        with patch.object(
            runtime.detection,
            "train_isolation_forest",
            side_effect=AssertionError(
                "Unexpected retraining"
            ),
        ):
            result = self.build_result()

        self.assertEqual(len(result), 40)

    def test_scores_are_batch_independent(self):
        full = self.build_result()

        for position in (0, 20):
            with self.subTest(position=position):
                single = build_pretrained_stability(
                    self.models_by_seed,
                    self.test_matrix.iloc[
                        [position]
                    ].reset_index(drop=True),
                    self.test_context.iloc[
                        [position]
                    ].reset_index(drop=True),
                )

                self.assertAlmostEqual(
                    float(
                        full.iloc[position][
                            "mean_anomaly_score"
                        ]
                    ),
                    float(
                        single.iloc[0][
                            "mean_anomaly_score"
                        ]
                    ),
                    places=12,
                )

                self.assertEqual(
                    int(
                        full.iloc[position]["flag_count"]
                    ),
                    int(single.iloc[0]["flag_count"]),
                )

    def test_missing_stability_seed_is_rejected(self):
        models = dict(self.models_by_seed)

        models.pop(RANDOM_STATES[0])

        with self.assertRaisesRegex(
            ValueError,
            "Expected one pretrained model",
        ):
            build_pretrained_stability(
                models,
                self.test_matrix,
                self.test_context,
            )

    def test_feature_contract_change_is_rejected(self):
        reordered = self.test_matrix.loc[
            :,
            list(reversed(self.test_matrix.columns)),
        ]

        with self.assertRaisesRegex(
            ValueError,
            "Feature contract differs from training",
        ):
            build_pretrained_stability(
                self.models_by_seed,
                reordered,
                self.test_context,
            )

    def test_zero_votes_are_valid(self):
        # Simulate models whose historical P95
        # is above every test score.
        modified_models = {}

        for seed, trained in self.models_by_seed.items():
            thresholds = {
                key: 2.0
                for key in trained.thresholds
            }

            modified_models[seed] = replace(
                trained,
                thresholds=thresholds,
            )

        result = build_pretrained_stability(
            modified_models,
            self.test_matrix,
            self.test_context,
        )

        self.assertTrue(
            result["flag_count"].eq(0).all()
        )

        self.assertTrue(
            result["stability_rate"].eq(0).all()
        )


if __name__ == "__main__":
    unittest.main()
