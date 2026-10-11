import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from src.loading import load_ke24_bigquery as loader

class TestKE24BigQueryReadOnly(unittest.TestCase):

    def test_main_blocks_without_authorization(self):
        with patch.object(
            loader,
            "create_bigquery_client",
        ) as client_factory:

            with self.assertRaises(RuntimeError):
                loader.main()

            client_factory.assert_not_called()

    def test_direct_write_is_disabled(self):
        client = Mock()
        fake_df = pd.DataFrame()

        # Simula validação aprovada somente no teste
        with patch.object(
            loader,
            "validate_bigquery_release",
        ) as validate:

            with self.assertRaisesRegex(
                RuntimeError,
                "Gravação BigQuery desabilitada",
            ):
                loader.load_to_bigquery(
                    client,
                    fake_df,
                )

            validate.assert_called_once_with(fake_df)

        client.get_table.assert_not_called()
        client.load_table_from_dataframe.assert_not_called()

    def test_main_only_generates_preview(self):
        client = Mock()

        fake_df = pd.DataFrame({
            "synthetic": [1, 2],
        })

        fake_plan = {
            "new_files": 1,
            "skipped_files": 1,
            "new_rows": fake_df.iloc[:1].copy(),
        }

        with tempfile.TemporaryDirectory() as directory:
            fake_path = Path(directory) / "fake_staging.parquet"
            fake_path.touch()

            with (
                patch.object(loader, "INPUT_FILE", fake_path),
                patch.object(
                    loader.pd,
                    "read_parquet",
                    return_value=fake_df,
                ),
                patch.object(
                    loader,
                    "validate_bigquery_release",
                ),
                patch.object(
                    loader,
                    "create_bigquery_client",
                    return_value=client,
                ),
                patch.object(
                    loader,
                    "preview_bigquery_incremental",
                    return_value=fake_plan,
                ) as preview,
                patch.object(
                    loader,
                    "load_to_bigquery",
                    side_effect=AssertionError(
                        "Gravação não deveria ser chamada."
                    ),
                ) as write,
            ):
                with redirect_stdout(io.StringIO()):
                    result = loader.main()

                preview.assert_called_once()
                write.assert_not_called()

        self.assertEqual(result["new_files"], 1)
        self.assertEqual(result["skipped_files"], 1)
        self.assertEqual(result["new_records"], 1)

        client.load_table_from_dataframe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
