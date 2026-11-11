import unittest

import numpy as np

from src.pipeline.run_ke24_synthetic import (
    TEST_KEY,
    load_json,
)

from src.ml.evaluate_ke24_synthetic_holdout import (
    generate_normal_data,
    prepare_features,
)

from src.privacy.anonymize_ke24 import (
    anonymize_ke24_dataframe,
)

from src.processing import prepare_ke24_processed as processing

from src.features.build_ke24_pretrained_inference_features import (
    build_pretrained_inference_features,
)


class TestKE24PretrainedInferenceFeatures(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        schema = load_json("ke24_schema.json")
        cls.policy = load_json(
            "ke24_privacy_policy.json"
        )

        raw_train = generate_normal_data(
            schema, 11, "train.xlsx"
        )

        _, cls.manifest = prepare_features(
            raw_train,
            schema,
            cls.policy,
        )

        raw_test = generate_normal_data(
            schema, 29, "test.xlsx"
        )

        protected = anonymize_ke24_dataframe(
            raw_test,
            cls.policy,
            schema,
            TEST_KEY,
        )

        processing.validate_input_data(protected)

        processed, _ = (
            processing.standardize_measure_types(
                protected.copy()
            )
        )

        processed = processing.add_period_key(
            processed
        )

        cls.processed = (
            processing.sort_processed_data(
                processed
            )
        )

    def build(self, processed=None):
        return build_pretrained_inference_features(
            self.processed
            if processed is None
            else processed,
            self.manifest,
            self.policy,
        )

    def test_training_feature_is_kept_when_zero(self):
        modified = self.processed.copy()
        modified["revenue"] = 0.0

        result = self.build(modified)

        self.assertIn("revenue", result.columns)
        self.assertTrue(
            result["revenue"].eq(0).all()
        )

    def test_missing_measure_is_rejected(self):
        modified = self.processed.drop(
            columns=["revenue"]
        )

        with self.assertRaisesRegex(
            ValueError,
            "Financial measure schema differs",
        ):
            self.build(modified)

    def test_new_measure_is_rejected(self):
        modified = self.processed.copy()
        modified["unknown_financial_measure"] = 1.0

        with self.assertRaisesRegex(
            ValueError,
            "Financial measure schema differs",
        ):
            self.build(modified)

    def test_new_movement_in_inactive_measure_is_rejected(self):
        inactive = self.manifest.loc[
            self.manifest["feature_type"].eq(
                "financial_measure"
            )
            & ~self.manifest["selected_for_model"],
            "feature",
        ]

        self.assertGreater(len(inactive), 0)

        modified = self.processed.copy()
        modified.loc[
            modified.index[0],
            inactive.iloc[0],
        ] = 100.0

        with self.assertRaisesRegex(
            ValueError,
            "Training-inactive measures",
        ):
            self.build(modified)

    def test_unprotected_context_is_rejected(self):
        modified = self.processed.copy()
        modified.loc[
            modified.index[0],
            "company_code",
        ] = "REAL_COMPANY"

        with self.assertRaises(ValueError):
            self.build(modified)

    def test_non_finite_measure_is_rejected(self):
        modified = self.processed.copy()
        modified.loc[
            modified.index[0],
            "revenue",
        ] = np.inf

        with self.assertRaisesRegex(
            ValueError,
            "Non-finite KE24 measures",
        ):
            self.build(modified)


if __name__ == "__main__":
    unittest.main()
