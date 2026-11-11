import unittest

from src.pipeline.run_ke24_synthetic import load_json

from src.ml.evaluate_ke24_synthetic_holdout import (
    generate_normal_data,
    prepare_features,
)

from src.ml import prepare_ke24_training_matrix as training

from src.ml.ke24_training_fingerprint import (
    compute_training_fingerprint,
)


class TestKE24TrainingFingerprint(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        schema = load_json("ke24_schema.json")
        policy = load_json(
            "ke24_privacy_policy.json"
        )

        raw = generate_normal_data(
            schema,
            11,
            "synthetic_train.xlsx",
        )

        cls.context, manifest = prepare_features(
            raw,
            schema,
            policy,
        )

        selected = training.get_selected_features(
            manifest
        )

        matrix = training.build_training_matrix(
            cls.context,
            selected,
        )

        cls.matrix = matrix.loc[
            :, matrix.nunique(dropna=False).gt(1)
        ]

    def fingerprint(self, matrix=None, context=None):
        return compute_training_fingerprint(
            self.matrix if matrix is None else matrix,
            self.context if context is None else context,
        )

    def test_fingerprint_is_deterministic(self):
        first = self.fingerprint()
        second = self.fingerprint()

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_row_reordering_preserves_fingerprint(self):
        positions = (
            self.matrix.sample(
                frac=1,
                random_state=42,
            ).index
        )

        reordered_matrix = (
            self.matrix.loc[positions]
            .reset_index(drop=True)
        )

        reordered_context = (
            self.context.loc[positions]
            .reset_index(drop=True)
        )

        self.assertEqual(
            self.fingerprint(),
            self.fingerprint(
                reordered_matrix,
                reordered_context,
            ),
        )

    def test_financial_change_changes_fingerprint(self):
        modified = self.matrix.copy()

        column = modified.columns[0]

        modified.loc[
            modified.index[0], column
        ] += 1.0

        self.assertNotEqual(
            self.fingerprint(),
            self.fingerprint(matrix=modified),
        )

    def test_source_change_changes_fingerprint(self):
        modified = self.context.copy()

        modified.loc[
            modified.index[0],
            "_source_file_sha256",
        ] = "TKN_" + "f" * 32

        self.assertNotEqual(
            self.fingerprint(),
            self.fingerprint(context=modified),
        )


if __name__ == "__main__":
    unittest.main()
