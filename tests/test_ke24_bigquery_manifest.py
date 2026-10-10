import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from src.loading import load_ke24_bigquery as loader

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_json(filename):
    with open(
        PROJECT_ROOT / "config" / filename,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


class TestKE24BigQueryManifest(unittest.TestCase):

    def setUp(self):
        self.policy = load_json("ke24_privacy_policy.json")
        self.schema = load_json("ke24_schema.json")

        self.client = Mock()

        required = [
            "_source_file_sha256",
            "_source_file",
            "_source_row_number",
            "company_code",
            "period_year",
            "currency",
        ]

        self.client.get_table.return_value.schema = [
            SimpleNamespace(name=column)
            for column in required
        ]

        self.client.query.return_value.result.return_value = []

    @contextmanager
    def protected_configuration(self):
        approved_policy = {
            **self.policy,
            "status": "APPROVED",
        }

        with (
            patch.object(
                loader,
                "BQ_TABLE",
                "KE24_staging_protected",
            ),
            patch.object(
                loader,
                "TABLE_ID",
                "datalake-pipeline.profitability_data."
                "KE24_staging_protected",
            ),
            patch.object(
                loader,
                "load_privacy_runtime",
                return_value=(
                    approved_policy,
                    self.schema,
                    b"x" * 32,
                ),
            ),
        ):
            yield

    def fake_staging(self, file_char, period):
        columns = self.schema["columns"]
        start = columns.index("sales_quantity")

        record = {
            column: "TEST_VALUE"
            for column in columns[:start]
        }

        for column in columns[start:]:
            record[column] = 0.0

        for column in self.policy["tokenize_dimensions"]:
            record[column] = "TKN_" + "d" * 32

        record.update({
            "company_code": "TKN_" + "c" * 32,
            "period": int(period[:2]),
            "period_year": period,
            "currency": "EUR",
            "_load_id": "SYNTHETIC_LOAD",
            "_source_file_sha256": "TKN_" + file_char * 32,
            "_source_system": "SAP_KE24",
            "_source_file": "TKN_" + file_char * 32,
            "_source_row_number": 2,
            "_ingested_at_utc": pd.Timestamp(
                "2026-01-01T00:00:00Z"
            ),
        })

        return pd.DataFrame([record])

    def remote_record(self, staging_df):
        row = staging_df.iloc[0]

        return {
            "_source_file_sha256": row["_source_file_sha256"],
            "_source_file": row["_source_file"],
            "company_code": row["company_code"],
            "period_year": row["period_year"],
            "currency": row["currency"],
            "record_count": len(staging_df),
            "distinct_row_count": len(staging_df),
        }

    def set_remote_records(self, records):
        self.client.query.return_value.result.return_value = (
            records
        )

    def test_legacy_destination_blocks_query(self):
        with self.assertRaises(RuntimeError):
            loader.get_loaded_file_manifest(self.client)

        self.client.get_table.assert_not_called()
        self.client.query.assert_not_called()

    def test_empty_remote_table_plans_first_load(self):
        incoming = self.fake_staging("a", "01.2026")

        with self.protected_configuration():
            plan = loader.preview_bigquery_incremental(
                self.client,
                incoming,
            )

        self.assertEqual(plan["new_files"], 1)
        self.assertEqual(plan["skipped_files"], 0)
        self.assertEqual(len(plan["new_rows"]), 1)

        self.client.load_table_from_dataframe.assert_not_called()

    def test_existing_file_is_skipped(self):
        incoming = self.fake_staging("a", "01.2026")

        self.set_remote_records([
            self.remote_record(incoming)
        ])

        with self.protected_configuration():
            plan = loader.preview_bigquery_incremental(
                self.client,
                incoming,
            )

        self.assertEqual(plan["new_files"], 0)
        self.assertEqual(plan["skipped_files"], 1)
        self.assertTrue(plan["new_rows"].empty)

        self.client.load_table_from_dataframe.assert_not_called()

    def test_replaced_month_is_blocked(self):
        original = self.fake_staging("a", "01.2026")
        corrected = self.fake_staging("b", "01.2026")

        self.set_remote_records([
            self.remote_record(original)
        ])

        with self.protected_configuration():
            with self.assertRaises(ValueError):
                loader.preview_bigquery_incremental(
                    self.client,
                    corrected,
                )

        self.client.load_table_from_dataframe.assert_not_called()

    def test_duplicate_remote_rows_are_blocked(self):
        incoming = self.fake_staging("a", "01.2026")

        record = self.remote_record(incoming)
        record["record_count"] = 2
        record["distinct_row_count"] = 1

        self.set_remote_records([record])

        with self.protected_configuration():
            with self.assertRaises(ValueError):
                loader.get_loaded_file_manifest(self.client)

    def test_remote_schema_missing_column_is_blocked(self):
        self.client.get_table.return_value.schema = [
            SimpleNamespace(name="company_code")
        ]

        with self.protected_configuration():
            with self.assertRaises(ValueError):
                loader.get_loaded_file_manifest(self.client)

        self.client.query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
