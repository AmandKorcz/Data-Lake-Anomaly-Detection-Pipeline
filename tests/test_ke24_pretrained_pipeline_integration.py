import tempfile
import unittest

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.pipeline.run_ke24_synthetic import (
    TEST_KEY,
    build_synthetic_rules,
    load_json,
)

from src.ml.evaluate_ke24_synthetic_holdout import (
    generate_normal_data,
    prepare_features,
)

from src.ml import prepare_ke24_training_matrix as training
from src.ml import ke24_grouped_model_runtime as runtime

from src.ml.ke24_model_store import (
    save_model_bundle,
)

from src.ml.evaluate_ke24_isolation_forest_stability import (
    RANDOM_STATES,
)

from src.ml.protect_ke24_alert_rules import (
    protect_company_rule_keys,
)

from src.pipeline.run_ke24_pretrained_inference import (
    execute_pretrained_pipeline,
    execute_pretrained_pipeline_to_files,
)


class TestKE24PretrainedPipelineIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)

        cls.root = Path(cls.temp.name)
        cls.model_root = cls.root / "models"

        schema = load_json("ke24_schema.json")
        cls.policy = load_json(
            "ke24_privacy_policy.json"
        )

        raw_train = generate_normal_data(
            schema, 11, "synthetic_train.xlsx"
        )

        raw_test = generate_normal_data(
            schema, 29, "synthetic_test.xlsx"
        )

        raw_test.loc[2, "revenue"] = 80000.0
        raw_test.loc[22, "revenue"] = 200000.0

        train_features, manifest = prepare_features(
            raw_train,
            schema,
            cls.policy,
        )

        cls.new_features, _ = prepare_features(
            raw_test,
            schema,
            cls.policy,
        )

        cls.feature_manifest = manifest

        selected = training.get_selected_features(
            manifest
        )

        matrix = training.build_training_matrix(
            train_features,
            selected,
        )

        feature_columns = matrix.columns[
            matrix.nunique(dropna=False).gt(1)
        ]

        matrix = matrix[feature_columns]

        cls.versions = {}
        cls.trained_models = {}

        # Explicit training phase: before inference.
        for seed in RANDOM_STATES:
            trained = runtime.train_grouped_models(
                matrix,
                train_features,
                random_state=seed,
            )

            version = f"synthetic-seed-{seed}"

            save_model_bundle(
                trained,
                cls.model_root,
                version,
            )

            cls.versions[seed] = version
            cls.trained_models[seed] = trained

        # Synthetic business rules only.
        rules = build_synthetic_rules()

        rules["technical_thresholds"].update({
            "pretrained_margin_medium_min": 0.0,
            "pretrained_margin_high_min": 0.05,
        })

        cls.rules = protect_company_rule_keys(
            rules,
            TEST_KEY,
        )

    def execute(
        self,
        versions=None,
        previous_history=None,
        new_features=None,
    ):
        return execute_pretrained_pipeline(
            self.new_features
            if new_features is None
            else new_features,
            self.feature_manifest,
            self.model_root,
            self.versions
            if versions is None
            else versions,
            self.rules,
            self.policy,
            previous_history=previous_history,
        )

    def test_end_to_end_creates_dashboard(self):
        result = self.execute()

        self.assertEqual(result["scored_count"], 40)

        self.assertGreater(
            result["candidate_count"], 0
        )

        self.assertEqual(
            len(result["history"]),
            result["candidate_count"],
        )

        self.assertEqual(
            len(result["dashboard"]),
            result["candidate_count"],
        )

        self.assertNotIn(
            "anomaly_score_percentile",
            result["dashboard"].columns,
        )

        self.assertIn(
            "model_version",
            result["dashboard"].columns,
        )

    def test_executor_never_trains(self):
        with patch.object(
            runtime.detection,
            "train_isolation_forest",
            side_effect=AssertionError(
                "Training during inference"
            ),
        ):
            result = self.execute()

        self.assertEqual(result["scored_count"], 40)

    def test_file_execution_preserves_investigation(self):
        with tempfile.TemporaryDirectory() as folder:
            history_file = Path(folder) / "history.csv"
            dashboard_file = Path(folder) / "dashboard.csv"

            execute_pretrained_pipeline_to_files(
                self.new_features,
                self.feature_manifest,
                self.model_root,
                self.versions,
                self.rules,
                self.policy,
                history_file,
                dashboard_file,
            )

            before = pd.read_csv(history_file)

            self.assertGreater(len(before), 0)

            alert_id = before.loc[0, "alert_id"]
            first_date = before.loc[
                0, "detected_at_utc"
            ]

            before.loc[
                0, "investigation_status"
            ] = "INVESTIGATING"

            before.to_csv(
                history_file,
                index=False,
                encoding="utf-8-sig",
            )

            execute_pretrained_pipeline_to_files(
                self.new_features,
                self.feature_manifest,
                self.model_root,
                self.versions,
                self.rules,
                self.policy,
                history_file,
                dashboard_file,
            )

            after = pd.read_csv(history_file)
            dashboard = pd.read_csv(dashboard_file)

            self.assertFalse(
                after["alert_id"].duplicated().any()
            )

            updated = after.set_index("alert_id")

            self.assertEqual(
                updated.loc[alert_id, "detected_at_utc"],
                first_date,
            )

            self.assertEqual(
                updated.loc[
                    alert_id, "investigation_status"
                ],
                "INVESTIGATING",
            )

            self.assertEqual(
                dashboard.set_index("alert_id")
                .loc[alert_id, "investigation_status"],
                "INVESTIGATING",
            )

    def test_no_candidates_is_valid(self):
        versions = {}

        # Force a no-alert scenario using fictional
        # thresholds in separate model versions.
        for seed, trained in self.trained_models.items():
            modified = replace(
                trained,
                thresholds={
                    key: 2.0
                    for key in trained.thresholds
                },
            )

            version = f"noalerts-seed-{seed}"

            save_model_bundle(
                modified,
                self.model_root,
                version,
            )

            versions[seed] = version

        result = self.execute(versions=versions)

        self.assertEqual(result["candidate_count"], 0)
        self.assertTrue(result["dashboard"].empty)

        previous = self.execute()["history"]

        preserved = self.execute(
            versions=versions,
            previous_history=previous,
        )

        self.assertEqual(
            len(preserved["history"]),
            len(previous),
        )

    def test_missing_model_version_is_rejected(self):
        versions = dict(self.versions)
        versions.pop(RANDOM_STATES[0])

        with self.assertRaisesRegex(
            ValueError,
            "Missing stability model versions",
        ):
            self.execute(versions=versions)

    def test_unprotected_input_is_rejected(self):
        modified = self.new_features.copy()

        modified.loc[
            0, "company_code"
        ] = "COMP_A"

        with self.assertRaises(ValueError):
            self.execute(new_features=modified)


if __name__ == "__main__":
    unittest.main()
