import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from src.pipeline.run_ke24_synthetic import load_json

from src.ml.evaluate_ke24_synthetic_holdout import (
    generate_normal_data,
    prepare_features,
)

from src.ml import prepare_ke24_training_matrix as training
from src.ml import profile_ke24_anomaly_materiality as materiality
from src.ml import ke24_grouped_model_runtime as runtime
from src.ml import ke24_model_store as store

from src.ml.build_ke24_pretrained_anomaly_results import (
    build_pretrained_anomaly_results,
)


class TestKE24PretrainedAnomalyResults(unittest.TestCase):

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

        active_columns = train_matrix.columns[
            train_matrix.nunique(dropna=False).gt(1)
        ]

        train_matrix = train_matrix[active_columns]

        cls.test_matrix = training.build_training_matrix(
            test_features,
            selected,
        )[active_columns]

        cls.test_context = test_features
        cls.feature_manifest = manifest

        trained = runtime.train_grouped_models(
            train_matrix,
            train_features,
        )

        # Simulate a separate process loading a saved model.
        with tempfile.TemporaryDirectory() as folder:
            store.save_model_bundle(
                trained,
                folder,
                "synthetic-16e1",
            )

            cls.trained, cls.model_manifest = (
                store.load_model_bundle(
                    folder,
                    "synthetic-16e1",
                )
            )

    def build_results(self):
        return build_pretrained_anomaly_results(
            self.trained,
            self.test_matrix,
            self.test_context,
            model_manifest=self.model_manifest,
        )

    def test_training_p95_is_used_for_signal(self):
        result = self.build_results()

        self.assertEqual(len(result), 40)

        self.assertTrue(
            (
                result["anomaly_flag"]
                == result["reference_p95_flag"]
            ).all()
        )

        self.assertTrue(
            (
                result["anomaly_flag"]
                == (
                    result["anomaly_score"]
                    >= result["training_p95_threshold"]
                )
            ).all()
        )

        self.assertEqual(
            set(result["signal_method"]),
            {"training_p95"},
        )

        self.assertEqual(
            set(result["model_version"]),
            {"synthetic-16e1"},
        )

    def test_ranking_is_grouped(self):
        result = self.build_results()

        ranked = pd.DataFrame({
            "currency": self.test_context[
                "currency"
            ].to_numpy(),
            "rank": result[
                "anomaly_rank"
            ].to_numpy(),
        })

        for _, group in ranked.groupby("currency"):
            self.assertEqual(
                sorted(group["rank"].tolist()),
                list(range(1, 21)),
            )

    def test_materiality_accepts_inference_results(self):
        result = self.build_results()

        financial = materiality.get_financial_features(
            self.feature_manifest
        )

        materiality.validate_inputs(
            self.test_context,
            result,
            financial,
        )

        profiled = materiality.build_materiality_dataset(
            self.test_context,
            result,
            financial,
        )

        self.assertEqual(len(profiled), 40)

        self.assertFalse(
            profiled["materiality_percentile"]
            .isna()
            .any()
        )

    def test_inference_does_not_retrain(self):
        with patch.object(
            runtime.detection,
            "train_isolation_forest",
            side_effect=AssertionError(
                "Unexpected retraining"
            ),
        ):
            result = self.build_results()

        self.assertEqual(len(result), 40)

    def test_incompatible_manifest_is_rejected(self):
        modified = dict(self.model_manifest)

        modified["feature_columns"] = list(
            reversed(modified["feature_columns"])
        )

        with self.assertRaisesRegex(
            ValueError,
            "Incompatible model manifest",
        ):
            build_pretrained_anomaly_results(
                self.trained,
                self.test_matrix,
                self.test_context,
                model_manifest=modified,
            )


if __name__ == "__main__":
    unittest.main()
