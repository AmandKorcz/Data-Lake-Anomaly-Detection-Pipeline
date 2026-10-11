import unittest
from unittest.mock import Mock, patch

import pandas as pd

from src.loading.ke24_batch_reconciliation import (
    assess_exact_batch,
    reconcile_observed_batch,
)
from src.loading.ke24_run_reconciliation import (
    JobObservation,
    LockObservation,
)

RUN_ID = "a" * 32


def fake_file(letter, month, company="c", count=2):
    return {
        "_source_file_sha256": "TKN_" + letter * 32,
        "company_code": "TKN_" + company * 32,
        "period": month,
        "period_year": "2026",
        "currency": "RUB",
        "record_count": count,
    }

class TestKE24BatchReconciliation(unittest.TestCase):

    def setUp(self):
        self.expected = pd.DataFrame([
            fake_file("a", 1),
            fake_file("b", 2),
        ])

        self.remote = self.expected.copy()

        self.journal = Mock()
        self.journal.run_id = RUN_ID

        self.journal.read.return_value = (
            {
                "run_id": RUN_ID,
                "state": "RUNNING",
                "lock_generation": 7,
            },
            1,
        )

        self.storage = Mock()
        self.bigquery = Mock()

    def observe(
        self,
        *,
        lock_status="PRESENT",
        job_status="SUCCEEDED",
        remote=None,
        owner=RUN_ID,
    ):
        if remote is None:
            remote = self.remote

        with (
            patch(
                "src.loading.ke24_batch_reconciliation."
                "observe_gcs_lock",
                return_value=LockObservation(
                    status=lock_status,
                    owner_run_id=owner,
                    generation=7,
                ),
            ) as observe_lock,
            patch(
                "src.loading.ke24_batch_reconciliation."
                "observe_bigquery_job",
                return_value=JobObservation(
                    status=job_status,
                    job_id="ke24_protected_" + RUN_ID,
                ),
            ) as observe_job,
        ):
            result = reconcile_observed_batch(
                run_journal=self.journal,
                storage_client=self.storage,
                bucket_name="test-bucket",
                bigquery_client=self.bigquery,
                expected_manifest=self.expected,
                remote_manifest=remote,
            )

            observe_lock.assert_called_once_with(
                self.storage,
                "test-bucket",
            )

            observe_job.assert_called_once_with(
                self.bigquery,
                RUN_ID,
            )

        return result

    def test_exact_batch_loaded(self):
        result = assess_exact_batch(
            self.expected,
            self.remote,
        )

        self.assertEqual(
            result,
            "ALREADY_LOADED",
        )

    def test_unrelated_remote_month_is_allowed(self):
        remote = pd.concat(
            [
                self.remote,
                pd.DataFrame([
                    fake_file("d", 3),
                ]),
            ],
            ignore_index=True,
        )

        result = assess_exact_batch(
            self.expected,
            remote,
        )

        self.assertEqual(
            result,
            "ALREADY_LOADED",
        )

    def test_one_expected_file_missing(self):
        result = assess_exact_batch(
            self.expected,
            self.remote.iloc[:1],
        )

        self.assertEqual(
            result,
            "NEW_FILES",
        )

    def test_empty_remote_is_not_loaded(self):
        result = assess_exact_batch(
            self.expected,
            self.remote.iloc[:0],
        )

        self.assertEqual(
            result,
            "NEW_FILES",
        )

    def test_changed_count_is_conflict(self):
        remote = self.remote.copy()
        remote.loc[0, "record_count"] = 1

        result = assess_exact_batch(
            self.expected,
            remote,
        )

        self.assertEqual(
            result,
            "CONFLICT",
        )

    def test_same_hash_changed_period_is_conflict(self):
        remote = self.remote.copy()
        remote.loc[0, "period"] = 5

        result = assess_exact_batch(
            self.expected,
            remote,
        )

        self.assertEqual(
            result,
            "CONFLICT",
        )

    def test_same_period_replaced_hash_is_conflict(self):
        remote = self.remote.copy()

        remote.loc[
            0,
            "_source_file_sha256",
        ] = "TKN_" + "d" * 32

        result = assess_exact_batch(
            self.expected,
            remote,
        )

        self.assertEqual(
            result,
            "CONFLICT",
        )

    def test_duplicate_remote_file_is_conflict(self):
        remote = pd.concat(
            [
                self.remote,
                self.remote.iloc[:1],
            ],
            ignore_index=True,
        )

        result = assess_exact_batch(
            self.expected,
            remote,
        )

        self.assertEqual(
            result,
            "CONFLICT",
        )

    def test_duplicate_expected_business_key_is_unverified(self):
        expected = self.expected.copy()
        expected.loc[1, "period"] = 1

        result = assess_exact_batch(
            expected,
            self.remote,
        )

        self.assertEqual(
            result,
            "UNVERIFIED",
        )

    def test_missing_remote_column_is_unverified(self):
        remote = self.remote.drop(
            columns="period"
        )

        result = assess_exact_batch(
            self.expected,
            remote,
        )

        self.assertEqual(
            result,
            "UNVERIFIED",
        )

    def test_invalid_token_is_unverified(self):
        remote = self.remote.copy()

        remote.loc[
            0,
            "company_code",
        ] = "raw-company"

        result = assess_exact_batch(
            self.expected,
            remote,
        )

        self.assertEqual(
            result,
            "UNVERIFIED",
        )

    def test_invalid_count_is_unverified(self):
        remote = self.remote.copy()
        remote.loc[0, "record_count"] = 0

        result = assess_exact_batch(
            self.expected,
            remote,
        )

        self.assertEqual(
            result,
            "UNVERIFIED",
        )

    def test_empty_expected_is_unverified(self):
        result = assess_exact_batch(
            self.expected.iloc[:0],
            self.remote,
        )

        self.assertEqual(
            result,
            "UNVERIFIED",
        )

    def test_valid_reconciliation_only_requests_review(self):
        result = self.observe()

        self.assertEqual(
            result.action,
            "REVIEW_REQUIRED",
        )

    def test_running_job_waits(self):
        result = self.observe(
            job_status="RUNNING"
        )

        self.assertEqual(
            result.action,
            "WAIT",
        )

    def test_failed_job_reconciles(self):
        result = self.observe(
            job_status="FAILED"
        )

        self.assertEqual(
            result.action,
            "RECONCILE",
        )

    def test_different_owner_blocks(self):
        result = self.observe(
            owner="b" * 32
        )

        self.assertEqual(
            result.action,
            "BLOCK",
        )

    def test_missing_batch_reconciles(self):
        result = self.observe(
            remote=self.remote.iloc[:0]
        )

        self.assertEqual(
            result.action,
            "RECONCILE",
        )

    def test_conflicting_batch_blocks(self):
        remote = self.remote.copy()
        remote.loc[0, "record_count"] = 1

        result = self.observe(
            remote=remote
        )

        self.assertEqual(
            result.action,
            "BLOCK",
        )

    def test_no_bigquery_or_storage_mutations(self):
        result = self.observe()

        self.assertEqual(
            result.action,
            "REVIEW_REQUIRED",
        )

        self.bigquery.query.assert_not_called()

        self.bigquery.load_table_from_dataframe.assert_not_called()

        self.storage.bucket.assert_not_called()

        self.journal.mark_unknown.assert_not_called()


if __name__ == "__main__":
    unittest.main()
