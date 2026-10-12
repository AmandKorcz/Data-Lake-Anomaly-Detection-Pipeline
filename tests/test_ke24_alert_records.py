import unittest
from datetime import datetime, timezone

import pandas as pd

from src.alerts.build_ke24_alert_records import (
    build_alert_records,
    generate_alert_id,
)

class TestKE24AlertRecords(unittest.TestCase):

    def setUp(self):
        self.dataset = pd.DataFrame([{
            "_source_file": "TKN_" + "a" * 32,
            "_source_file_sha256": "TKN_" + "b" * 32,
            "_source_row_number": 2,
            "company_code": "TKN_" + "c" * 32,
            "reference_document": "TKN_" + "d" * 32,
            "period_key": "2026-10",
            "alert_level": "HIGH",
            "classification_status": "EXPERIMENTAL_CLASSIFICATION",
        }])

        self.detected_at = datetime(
            2026, 10, 11, 22, 0,
            tzinfo=timezone.utc,
        )

    def build_records(self, dataset=None, timestamp=None):
        return build_alert_records(
            self.dataset if dataset is None else dataset,
            self.detected_at if timestamp is None else timestamp,
        )

    def test_creates_alert_record(self):
        result = self.build_records()

        self.assertEqual(len(result), 1)
        self.assertTrue(
            result.loc[0, "alert_id"].startswith("ALT_")
        )
        self.assertEqual(
            result.loc[0, "investigation_status"],
            "DETECTED",
        )
        self.assertEqual(
            result.loc[0, "alert_level"],
            "HIGH",
        )

    def test_same_record_generates_same_id(self):
        first = self.build_records()

        second = self.build_records(
            timestamp=datetime(
                2026, 10, 12, 10, 0,
                tzinfo=timezone.utc,
            )
        )

        self.assertEqual(
            first.loc[0, "alert_id"],
            second.loc[0, "alert_id"],
        )

    def test_priority_change_preserves_alert_id(self):
        first = self.build_records()

        changed = self.dataset.copy()
        changed.loc[0, "alert_level"] = "MEDIUM"

        second = self.build_records(changed)

        self.assertEqual(
            first.loc[0, "alert_id"],
            second.loc[0, "alert_id"],
        )

    def test_different_source_version_changes_id(self):
        first = self.build_records()

        changed = self.dataset.copy()
        changed.loc[0, "_source_file_sha256"] = (
            "TKN_" + "e" * 32
        )

        second = self.build_records(changed)

        self.assertNotEqual(
            first.loc[0, "alert_id"],
            second.loc[0, "alert_id"],
        )

    def test_different_source_row_changes_id(self):
        first = self.build_records()

        changed = self.dataset.copy()
        changed.loc[0, "_source_row_number"] = 3

        second = self.build_records(changed)

        self.assertNotEqual(
            first.loc[0, "alert_id"],
            second.loc[0, "alert_id"],
        )

    def test_rejects_original_company_identifier(self):
        changed = self.dataset.copy()
        changed.loc[0, "company_code"] = "COMP_A"

        with self.assertRaisesRegex(
            ValueError, "não protegidos"
        ):
            self.build_records(changed)

    def test_rejects_unprotected_document(self):
        changed = self.dataset.copy()
        changed.loc[0, "reference_document"] = "DOC_001"

        with self.assertRaisesRegex(
            ValueError, "não protegidos"
        ):
            self.build_records(changed)

    def test_rejects_duplicate_source_records(self):
        duplicated = pd.concat(
            [self.dataset, self.dataset],
            ignore_index=True,
        )

        with self.assertRaisesRegex(
            ValueError, "duplicados"
        ):
            self.build_records(duplicated)

    def test_rejects_invalid_alert_level(self):
        changed = self.dataset.copy()
        changed.loc[0, "alert_level"] = "CRITICAL"

        with self.assertRaisesRegex(
            ValueError, "níveis de alerta"
        ):
            self.build_records(changed)

    def test_rejects_invalid_classification_status(self):
        changed = self.dataset.copy()
        changed.loc[0, "classification_status"] = "UNKNOWN"

        with self.assertRaisesRegex(
            ValueError, "status de classificação"
        ):
            self.build_records(changed)

    def test_rejects_timestamp_without_timezone(self):
        with self.assertRaisesRegex(
            ValueError, "timezone"
        ):
            self.build_records(
                timestamp=datetime(2026, 10, 11, 22, 0)
            )

    def test_accepts_missing_reference_document(self):
        changed = self.dataset.copy()
        changed.loc[0, "reference_document"] = pd.NA

        result = self.build_records(changed)

        self.assertIsNone(
            result.loc[0, "reference_document"]
        )

    def test_original_dataset_is_not_modified(self):
        original = self.dataset.copy(deep=True)

        self.build_records()

        pd.testing.assert_frame_equal(
            self.dataset, original
        )

    def test_rejects_missing_required_column(self):
        changed = self.dataset.drop(
            columns=["company_code"]
        )

        with self.assertRaisesRegex(
            ValueError, "Colunas obrigatórias"
        ):
            self.build_records(changed)

if __name__ == "__main__":
    unittest.main()