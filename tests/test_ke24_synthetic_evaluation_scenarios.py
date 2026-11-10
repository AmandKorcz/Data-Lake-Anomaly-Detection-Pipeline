
import unittest

import numpy as np
import pandas as pd

from src.pipeline.run_ke24_synthetic import load_json

from src.ml.evaluate_ke24_synthetic_multiple_holdouts import (
    ANOMALY_VALUES,
    RELATIVE_FEATURES,
    add_relative_features,
    inject_known_anomalies,
    prepare_batch,
)


class TestKE24SyntheticEvaluationScenarios(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.schema = load_json("ke24_schema.json")
        cls.original = prepare_batch(
            cls.schema,
            29,
            "test",
        )

    def test_four_known_anomalies_per_difficulty(self):
        for difficulty in ANOMALY_VALUES:
            with self.subTest(difficulty=difficulty):
                modified, known = inject_known_anomalies(
                    self.original,
                    29,
                    difficulty,
                )

                self.assertEqual(len(modified), 40)
                self.assertEqual(len(known), 4)

                self.assertEqual(
                    sum(case.startswith("BRL_")
                        for case in known.values()),
                    2,
                )

                self.assertEqual(
                    sum(case.startswith("USD_")
                        for case in known.values()),
                    2,
                )

    def test_injection_does_not_modify_original(self):
        before = self.original.copy(deep=True)

        inject_known_anomalies(
            self.original,
            29,
            "subtle",
        )

        pd.testing.assert_frame_equal(
            self.original,
            before,
        )

    def test_anomaly_positions_are_reproducible(self):
        first, first_known = inject_known_anomalies(
            self.original,
            29,
            "subtle",
        )

        second, second_known = inject_known_anomalies(
            self.original,
            29,
            "subtle",
        )

        self.assertEqual(first_known, second_known)

        pd.testing.assert_frame_equal(first, second)

    def test_anomaly_positions_vary_across_batches(self):
        positions = set()

        for seed in range(29, 39):
            batch = prepare_batch(
                self.schema,
                seed,
                "test",
            )

            _, known = inject_known_anomalies(
                batch,
                seed,
                "subtle",
            )

            positions.add(tuple(sorted(known)))

        self.assertGreater(len(positions), 1)

    def test_relative_features_are_finite(self):
        result = add_relative_features(
            self.original
        )

        self.assertTrue(
            set(RELATIVE_FEATURES).issubset(
                result.columns
            )
        )

        values = result[
            RELATIVE_FEATURES
        ].to_numpy(dtype=float)

        self.assertTrue(
            np.isfinite(values).all()
        )

        self.assertFalse(
            set(RELATIVE_FEATURES).intersection(
                self.original.columns
            )
        )

    def test_labels_are_separate_from_model_data(self):
        modified, known = inject_known_anomalies(
            self.original,
            29,
            "subtle",
        )

        self.assertEqual(len(known), 4)

        self.assertNotIn(
            "known_anomaly",
            modified.columns,
        )

        self.assertNotIn(
            "anomaly_case",
            modified.columns,
        )


if __name__ == "__main__":
    unittest.main()
