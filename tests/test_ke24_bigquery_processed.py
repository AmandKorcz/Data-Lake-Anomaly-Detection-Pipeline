from unittest import TestCase
from unittest.mock import Mock, patch

import pandas as pd
from google.cloud import bigquery

from src.pipeline import run_ke24_bigquery_processed as pipeline


class TestKe24BigQueryProcessed(TestCase):
    def setUp(self):
        self.processed = pd.DataFrame(
            {
                "company_code": ["TKN_" + "a" * 32] * 2,
                "period_key": ["2026-03", "2026-04"],
                "currency": ["BRL", "BRL"],
                "period_date": pd.to_datetime(["2026-03-01", "2026-04-01"]),
                "sales_quantity": [1, 2],
            }
        )
        self.expected_counts = {
            key: 1
            for key in self.processed[list(pipeline.GROUP_COLUMNS)].itertuples(
                index=False, name=None
            )
        }

    def test_preview_does_not_connect_to_bigquery(self):
        with (
            patch.object(pipeline, "load_privacy_runtime", return_value=({}, {}, None)),
            patch.object(pipeline, "authenticate"),
            patch.object(pipeline, "build"),
            patch.object(
                pipeline, "load_verified_processed_dataframe", return_value=self.processed
            ),
            patch.object(pipeline.bigquery, "Client") as bigquery_client,
        ):
            pipeline.main([])

        bigquery_client.assert_not_called()

    def test_publish_replaces_table_and_checks_each_group(self):
        client = Mock()
        client.query.return_value.result.return_value = [
            dict(zip(pipeline.GROUP_COLUMNS, key), record_count=count)
            for key, count in self.expected_counts.items()
        ]

        def load(parquet, table_id, *, rewind, job_config, location):
            self.assertEqual(table_id, "datalake-pipeline.profitability_data.ke24_processed")
            self.assertEqual(location, "southamerica-east1")
            self.assertTrue(rewind)
            self.assertEqual(job_config.source_format, bigquery.SourceFormat.PARQUET)
            self.assertEqual(
                job_config.write_disposition, bigquery.WriteDisposition.WRITE_TRUNCATE
            )
            parquet.seek(0)
            pd.testing.assert_frame_equal(
                pd.read_parquet(parquet), self.processed, check_dtype=False
            )
            return Mock()

        client.load_table_from_file.side_effect = load
        pipeline.publish_processed_snapshot(client, self.processed, self.expected_counts)

        query = client.query.call_args.args[0]
        self.assertIn("GROUP BY company_code, period_key, currency", query)

        client.query.return_value.result.return_value[0]["record_count"] = 2
        client.query.return_value.result.return_value[1]["record_count"] = 0
        with self.assertRaisesRegex(RuntimeError, "divergiram"):
            pipeline.publish_processed_snapshot(
                client, self.processed, self.expected_counts
            )
