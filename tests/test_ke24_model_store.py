import json
import tempfile
import unittest

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.pipeline.run_ke24_synthetic import load_json

from src.ml.evaluate_ke24_synthetic_holdout import (
    generate_normal_data,
    prepare_features,
)

from src.ml import prepare_ke24_training_matrix as training
from src.ml import ke24_grouped_model_runtime as runtime
from src.ml import ke24_model_store as store


class TestKE24ModelStore(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        schema = load_json("ke24_schema.json")
        policy = load_json("ke24_privacy_policy.json")

        raw_train = generate_normal_data(
            schema, 11, "synthetic_train.xlsx"
        )
        raw_test = generate_normal_data(
            schema, 29, "synthetic_test.xlsx"
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

        # Train once for the entire test class.
        cls.trained = runtime.train_grouped_models(
            cls.train_matrix,
            cls.train_context,
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.root = Path(self.temp.name)
        self.version = "synthetic-v1"

    def save(self):
        return store.save_model_bundle(
            self.trained,
            self.root,
            self.version,
        )

    def test_save_and_restore_same_predictions(self):
        before = runtime.score_new_records(
            self.trained,
            self.test_matrix,
            self.test_context,
        )

        self.save()

        restored, manifest = store.load_model_bundle(
            self.root,
            self.version,
        )

        after = runtime.score_new_records(
            restored,
            self.test_matrix,
            self.test_context,
        )

        pd.testing.assert_frame_equal(before, after)

        self.assertEqual(
            manifest["model_version"],
            self.version,
        )
        self.assertEqual(manifest["group_count"], 2)
        self.assertEqual(
            manifest["threshold_method"],
            "training_p95",
        )

    def test_inference_does_not_retrain_after_load(self):
        self.save()

        with patch.object(
            runtime.detection,
            "train_isolation_forest",
            side_effect=AssertionError(
                "Unexpected retraining"
            ),
        ):
            restored, _ = store.load_model_bundle(
                self.root,
                self.version,
            )

            result = runtime.score_new_records(
                restored,
                self.test_matrix,
                self.test_context,
            )

        self.assertEqual(len(result), 40)

    def test_existing_version_cannot_be_overwritten(self):
        directory = self.save()

        original_hash = store.calculate_sha256(
            directory / store.MODEL_FILE
        )

        with self.assertRaises(FileExistsError):
            self.save()

        self.assertEqual(
            store.calculate_sha256(
                directory / store.MODEL_FILE
            ),
            original_hash,
        )

    def test_corrupted_model_is_rejected_before_load(self):
        directory = self.save()
        model_path = directory / store.MODEL_FILE

        with model_path.open("ab") as file:
            file.write(b"CORRUPTED_TEST_DATA")

        with patch.object(
            store.joblib,
            "load",
            side_effect=AssertionError(
                "Untrusted model was loaded"
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "integrity check failed",
            ):
                store.load_model_bundle(
                    self.root,
                    self.version,
                )

    def test_incompatible_environment_is_rejected(self):
        directory = self.save()

        manifest_path = directory / store.MANIFEST_FILE
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )

        manifest["sklearn_version"] = "0.0.0"

        manifest_path.write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )

        with patch.object(
            store.joblib,
            "load",
            side_effect=AssertionError(
                "Incompatible model was loaded"
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Incompatible model metadata",
            ):
                store.load_model_bundle(
                    self.root,
                    self.version,
                )

    def test_modified_feature_contract_is_rejected(self):
        directory = self.save()

        manifest_path = directory / store.MANIFEST_FILE
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )

        self.assertGreater(
            len(manifest["feature_columns"]),
            1,
        )

        manifest["feature_columns"] = list(
            reversed(manifest["feature_columns"])
        )

        manifest_path.write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ValueError,
            "Stored feature contract mismatch",
        ):
            store.load_model_bundle(
                self.root,
                self.version,
            )

    def test_invalid_version_names_are_rejected(self):
        invalid_versions = [
            "",
            "../escape",
            "a/b",
            ".hidden",
        ]

        for version in invalid_versions:
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    store.save_model_bundle(
                        self.trained,
                        self.root,
                        version,
                    )

    def test_invalid_model_groups_are_rejected(self):
        invalid_bundle = replace(
            self.trained,
            thresholds={},
        )

        with self.assertRaisesRegex(
            ValueError,
            "Inconsistent model groups",
        ):
            store.save_model_bundle(
                invalid_bundle,
                self.root,
                self.version,
            )

    def test_missing_model_file_is_rejected(self):
        directory = self.save()

        (directory / store.MODEL_FILE).unlink()

        with self.assertRaises(FileNotFoundError):
            store.load_model_bundle(
                self.root,
                self.version,
            )


if __name__ == "__main__":
    unittest.main()
