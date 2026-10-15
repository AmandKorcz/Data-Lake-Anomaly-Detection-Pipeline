import unittest

import pandas as pd

from src.ml.validate_ke24_business_validation import (
    validate_structure,
    validate_review_status,
    validate_reviwed_records,
    validate_pending_records,
)

class TestKE24BusinessReviewRules(unittest.TestCase):

    def setUp(self):
        self.dataset = pd.DataFrame([{
            "_source_file": "TKN_" + "a" * 32,
            "_source_row_number": 2,
            "reference_document": "TKN_" + "b" * 32,
            "review_status": "validated",
            "business_validation": "TRUE_ANOMALY",
            "validation_reason": "Divergência financeira fictícia",
            "validated_by": "TEST_REVIEWER",
            "validated_at": "2026-10-01T12:00:00Z",
            "business_notes": "Registro sintético",
        }])

    def test_valid_review_passes(self):
        validate_structure(self.dataset)
        validate_review_status(self.dataset)
        validate_reviwed_records(self.dataset)

    def test_review_status_is_case_insensitive(self):
        self.dataset.loc[0, "review_status"] = " VALIDATED "

        validate_review_status(self.dataset)
        validate_reviwed_records(self.dataset)

    def test_reject_invalid_business_validation(self):
        self.dataset.loc[0, "business_validation"] = "INVALID"

        with self.assertRaisesRegex(
            ValueError, "business_validation inválido"
        ):
            validate_reviwed_records(self.dataset)

    def test_reject_missing_validation_reason(self):
        self.dataset.loc[0, "validation_reason"] = ""

        with self.assertRaisesRegex(
            ValueError, "validation_reason"
        ):
            validate_reviwed_records(self.dataset)

    def test_reject_missing_reviewer(self):
        self.dataset.loc[0, "validated_by"] = pd.NA

        with self.assertRaisesRegex(
            ValueError, "validated_by"
        ):
            validate_reviwed_records(self.dataset)

    def test_reject_invalid_validation_date(self):
        self.dataset.loc[0, "validated_at"] = "invalid-date"

        with self.assertRaisesRegex(
            ValueError, "validated_at"
        ):
            validate_reviwed_records(self.dataset)

    def test_reject_pending_record_with_business_validation(self):
        self.dataset.loc[0, "review_status"] = "pending"

        with self.assertRaises(ValueError):
            validate_pending_records(self.dataset)

    def test_accept_pending_record_without_validation(self):
        self.dataset.loc[0, "review_status"] = "pending"
        self.dataset.loc[0, "business_validation"] = pd.NA

        validate_review_status(self.dataset)
        validate_pending_records(self.dataset)


if __name__ == "__main__":
    unittest.main()
