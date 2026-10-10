import copy
import json
import unittest
from pathlib import Path

from src.loading.build_ke24_transaction_sql import (
    build_transaction_sql,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

BUFFER_NAME = (
    "KE24_staging_protected_buffer_"
    + "a" * 32
)

def load_json(filename):
    with open(
        PROJECT_ROOT / "config" / filename,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)

class TestKE24TransactionSQL(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.schema = load_json("ke24_schema.json")
        cls.policy = load_json("ke24_privacy_policy.json")

    def build_sql(self):
        return build_transaction_sql(
            self.schema,
            self.policy,
            BUFFER_NAME,
        )

    def test_transaction_structure(self):
        sql = self.build_sql()

        self.assertIn("BEGIN TRANSACTION;", sql)
        self.assertIn("COMMIT TRANSACTION;", sql)
        self.assertIn("MERGE", sql)
        self.assertIn("WHEN NOT MATCHED THEN", sql)

        self.assertNotIn("WRITE_APPEND", sql)
        self.assertNotIn("WRITE_TRUNCATE", sql)

    def test_protected_tables_only(self):
        sql = self.build_sql()

        self.assertIn(
            "KE24_staging_protected_buffer_",
            sql,
        )

        self.assertIn(
            "`datalake-pipeline.profitability_data."
            "KE24_staging_protected`",
            sql,
        )

        self.assertNotIn(
            "`datalake-pipeline.profitability_data."
            "KE24_staging`",
            sql,
        )

    def test_financial_columns_are_included(self):
        sql = self.build_sql()

        self.assertIn("s.`revenue`", sql)
        self.assertIn("s.`sales_quantity`", sql)
        self.assertIn("s.`company_code`", sql)

        for column in self.schema["columns"]:
            self.assertIn(f"s.`{column}`", sql)

    def test_transaction_validates_month_and_rows(self):
        sql = self.build_sql()

        self.assertIn(
            "GROUP BY company_code, period, period_year, currency",
            sql,
        )

        self.assertIn(
            "record_count != distinct_row_count",
            sql,
        )

        self.assertIn(
            "s._source_row_number",
            sql,
        )

        self.assertGreaterEqual(
            sql.count("ASSERT NOT EXISTS"),
            7,
        )

    def test_invalid_buffer_name_is_rejected(self):
        with self.assertRaises(ValueError):
            build_transaction_sql(
                self.schema,
                self.policy,
                "KE24_staging",
            )

    def test_invalid_schema_or_column_is_rejected(self):
        invalid_schema = copy.deepcopy(self.schema)
        invalid_schema["schema_version"] = "999"

        with self.assertRaises(ValueError):
            build_transaction_sql(
                invalid_schema,
                self.policy,
                BUFFER_NAME,
            )

        invalid_schema = copy.deepcopy(self.schema)
        invalid_schema["columns"][1] = "currency; DROP TABLE x"

        with self.assertRaises(ValueError):
            build_transaction_sql(
                invalid_schema,
                self.policy,
                BUFFER_NAME,
            )

if __name__ == "__main__":
    unittest.main()
