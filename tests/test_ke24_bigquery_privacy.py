import json
import unittest
from pathlib import Path
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


class TestKE24BigQueryPrivacy(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.policy = load_json("ke24_privacy_policy.json")
        cls.schema = load_json("ke24_schema.json")

    def test_legacy_table_is_blocked(self):
        client = Mock()

        with self.assertRaises(RuntimeError):
            loader.load_to_bigquery(
                client,
                pd.DataFrame(),
            )

        client.get_table.assert_not_called()
        client.load_table_from_dataframe.assert_not_called()

    def test_draft_policy_blocks_release(self):
        protected_table = "KE24_staging_protected"

        with (
            patch.object(loader, "BQ_TABLE", protected_table),
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
                    self.policy,
                    self.schema,
                    b"x" * 32,
                ),
            ),
        ):
            with self.assertRaises(RuntimeError):
                loader.validate_bigquery_release()

    def test_raw_identifiers_are_rejected(self):
        protected_table = "KE24_staging_protected"

        # Monta um registro fictício com identificadores
        # originais, sem passar pela tokenização.
        record = {
            column: "EXEMPLO"
            for column in self.schema["columns"]
        }

        for column in self.policy["metadata_actions"]:
            record[column] = "EXEMPLO"

        record["company_code"] = "COMP_A"
        record["_source_file"] = "arquivo_original.xlsx"
        record["_source_file_sha256"] = "HASH_ORIGINAL"

        raw_df = pd.DataFrame([record])

        approved_policy = {
            **self.policy,
            "status": "APPROVED",
        }

        with (
            patch.object(loader, "BQ_TABLE", protected_table),
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
            with self.assertRaises(ValueError):
                loader.validate_bigquery_release(raw_df)


if __name__ == "__main__":
    unittest.main()
