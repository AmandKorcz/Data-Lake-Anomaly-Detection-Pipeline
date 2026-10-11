import unittest

from types import SimpleNamespace
from unittest.mock import Mock

from google.api_core.exceptions import (
    NotFound,
    PreconditionFailed,
)

from src.loading.gcs_execution_lock import (
    LOCK_OBJECT_NAME,
)

from src.loading.ke24_run_reconciliation import (
    expected_job_id,
)

from src.loading.ke24_state_observers import (
    BQ_LOCATION,
    BQ_PROJECT,
    observe_gcs_lock,
    observe_bigquery_job,
)

RUN_ID = "a" * 32
OTHER_ID = "b" * 32

class TestKE24StateObservers(unittest.TestCase):

    def setUp(self):
        # Simulação do Google Cloud Storage
        self.storage = Mock()

        self.blob = (
            self.storage
            .bucket.return_value
            .blob.return_value
        )

        self.blob.generation = 7
        self.blob.download_as_text.return_value = RUN_ID

        # Simulação do BigQuery
        self.bigquery = Mock()

        self.job = SimpleNamespace(
            job_id=expected_job_id(RUN_ID),
            project=BQ_PROJECT,
            location=BQ_LOCATION,
            job_type="query",
            state="DONE",
            error_result=None,
        )

        self.bigquery.get_job.return_value = self.job

    def test_lock_is_present(self):
        result = observe_gcs_lock(
            self.storage,
            "fake-test-bucket",
        )

        self.assertEqual(result.status, "PRESENT")
        self.assertEqual(result.owner_run_id, RUN_ID)
        self.assertEqual(result.generation, 7)

        self.storage.bucket.assert_called_once_with(
            "fake-test-bucket"
        )

        (
            self.storage.bucket.return_value.blob
            .assert_called_once_with(LOCK_OBJECT_NAME)
        )

        self.blob.download_as_text.assert_called_once_with(
            if_generation_match=7
        )

        self.blob.delete.assert_not_called()
        self.blob.upload_from_string.assert_not_called()

    def test_lock_is_missing(self):
        self.blob.reload.side_effect = NotFound(
            "Objeto não encontrado."
        )

        result = observe_gcs_lock(
            self.storage,
            "fake-test-bucket",
        )

        self.assertEqual(result.status, "MISSING")
        self.blob.download_as_text.assert_not_called()

    def test_other_owner_is_observed_without_assuming_ownership(self):
        self.blob.download_as_text.return_value = OTHER_ID

        result = observe_gcs_lock(
            self.storage,
            "fake-test-bucket",
        )

        self.assertEqual(result.status, "PRESENT")
        self.assertEqual(result.owner_run_id, OTHER_ID)

    def test_changed_lock_is_unknown(self):
        self.blob.download_as_text.side_effect = (
            PreconditionFailed("Geração alterada.")
        )

        result = observe_gcs_lock(
            self.storage,
            "fake-test-bucket",
        )

        self.assertEqual(result.status, "UNKNOWN")

    def test_malformed_owner_is_unknown(self):
        self.blob.download_as_text.return_value = (
            "invalid-owner"
        )

        result = observe_gcs_lock(
            self.storage,
            "fake-test-bucket",
        )

        self.assertEqual(result.status, "UNKNOWN")

    def test_invalid_generation_is_unknown(self):
        self.blob.generation = 0

        result = observe_gcs_lock(
            self.storage,
            "fake-test-bucket",
        )

        self.assertEqual(result.status, "UNKNOWN")

    def test_gcs_error_is_unknown(self):
        self.blob.reload.side_effect = (
            ConnectionError("Falha fictícia.")
        )

        result = observe_gcs_lock(
            self.storage,
            "fake-test-bucket",
        )

        self.assertEqual(result.status, "UNKNOWN")

    def test_job_succeeded(self):
        result = observe_bigquery_job(
            self.bigquery,
            RUN_ID,
        )

        self.assertEqual(result.status, "SUCCEEDED")

        self.bigquery.get_job.assert_called_once_with(
            expected_job_id(RUN_ID),
            project=BQ_PROJECT,
            location=BQ_LOCATION,
        )

        self.bigquery.query.assert_not_called()

        self.bigquery.load_table_from_dataframe.assert_not_called()

    def test_pending_and_running_jobs_are_active(self):
        for state in ("PENDING", "RUNNING"):
            with self.subTest(state=state):
                self.job.state = state

                result = observe_bigquery_job(
                    self.bigquery,
                    RUN_ID,
                )

                self.assertEqual(result.status, "RUNNING")

    def test_failed_job(self):
        self.job.error_result = {
            "reason": "internalError"
        }

        result = observe_bigquery_job(
            self.bigquery,
            RUN_ID,
        )

        self.assertEqual(result.status, "FAILED")

    def test_missing_job(self):
        self.bigquery.get_job.side_effect = NotFound(
            "Job inexistente."
        )

        result = observe_bigquery_job(
            self.bigquery,
            RUN_ID,
        )

        self.assertEqual(result.status, "NOT_FOUND")

    def test_bigquery_error_is_unknown(self):
        self.bigquery.get_job.side_effect = (
            TimeoutError("Falha fictícia.")
        )

        result = observe_bigquery_job(
            self.bigquery,
            RUN_ID,
        )

        self.assertEqual(result.status, "UNKNOWN")

    def test_wrong_job_identity_is_unknown(self):
        invalid_values = (
            ("job_id", expected_job_id(OTHER_ID)),
            ("project", "other-project"),
            ("location", "us"),
        )

        original_values = {
            "job_id": expected_job_id(RUN_ID),
            "project": BQ_PROJECT,
            "location": BQ_LOCATION,
        }

        for field, value in invalid_values:
            with self.subTest(field=field):
                setattr(self.job, field, value)

                result = observe_bigquery_job(
                    self.bigquery,
                    RUN_ID,
                )

                self.assertEqual(
                    result.status,
                    "UNKNOWN",
                )

                setattr(
                    self.job,
                    field,
                    original_values[field],
                )

    def test_unexpected_job_type_is_unknown(self):
        self.job.job_type = "load"

        result = observe_bigquery_job(
            self.bigquery,
            RUN_ID,
        )

        self.assertEqual(result.status, "UNKNOWN")

    def test_unexpected_job_state_is_unknown(self):
        self.job.state = "CANCELLED"

        result = observe_bigquery_job(
            self.bigquery,
            RUN_ID,
        )

        self.assertEqual(result.status, "UNKNOWN")

    def test_invalid_run_id_rejected_before_get_job(self):
        with self.assertRaises(ValueError):
            observe_bigquery_job(
                self.bigquery,
                "../wrong",
            )

        self.bigquery.get_job.assert_not_called()


if __name__ == "__main__":
    unittest.main()