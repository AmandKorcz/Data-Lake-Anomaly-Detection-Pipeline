import unittest
from unittest.mock import Mock

from src.loading.ke24_run_reconciliation import (
    LockObservation,
    JobObservation,
    expected_job_id,
    reconcile_registered_run,
)

RUN_ID = "a" * 32

class TestKE24RunReconciliation(unittest.TestCase):

    def setUp(self):
        self.journal = Mock()
        self.journal.run_id = RUN_ID

        self.journal.read.return_value = (
            {
                "run_id": RUN_ID,
                "state": "RUNNING",
                "lock_generation": 7,
            },
            2,
        )

    def decide(
        self,
        *,
        lock_status="PRESENT",
        owner=RUN_ID,
        generation=7,
        job_status="SUCCEEDED",
        job_id=None,
        manifest_status="ALREADY_LOADED",
    ):
        return reconcile_registered_run(
            run_journal=self.journal,
            lock_observation=LockObservation(
                status=lock_status,
                owner_run_id=owner,
                generation=generation,
            ),
            job_observation=JobObservation(
                status=job_status,
                job_id=job_id or expected_job_id(RUN_ID),
            ),
            manifest_status=manifest_status,
        )

    def test_success_is_review_only(self):
        result = self.decide()

        self.assertEqual(
            result.action,
            "REVIEW_REQUIRED",
        )

    def test_running_job_waits(self):
        result = self.decide(
            job_status="RUNNING"
        )

        self.assertEqual(result.action, "WAIT")

    def test_unknown_job_reconciles(self):
        result = self.decide(
            job_status="UNKNOWN"
        )

        self.assertEqual(result.action, "RECONCILE")

    def test_failed_job_reconciles(self):
        result = self.decide(
            job_status="FAILED"
        )

        self.assertEqual(result.action, "RECONCILE")

    def test_not_found_job_reconciles(self):
        result = self.decide(
            job_status="NOT_FOUND"
        )

        self.assertEqual(result.action, "RECONCILE")

    def test_manifest_unverified_reconciles(self):
        result = self.decide(
            manifest_status="UNVERIFIED"
        )

        self.assertEqual(result.action, "RECONCILE")

    def test_manifest_missing_files_reconciles(self):
        result = self.decide(
            manifest_status="NEW_FILES"
        )

        self.assertEqual(result.action, "RECONCILE")

    def test_manifest_conflict_blocks(self):
        result = self.decide(
            manifest_status="CONFLICT"
        )

        self.assertEqual(result.action, "BLOCK")

    def test_missing_lock_reconciles(self):
        result = self.decide(
            lock_status="MISSING"
        )

        self.assertEqual(result.action, "RECONCILE")

    def test_unknown_lock_reconciles(self):
        result = self.decide(
            lock_status="UNKNOWN"
        )

        self.assertEqual(result.action, "RECONCILE")

    def test_other_owner_blocks(self):
        result = self.decide(
            owner="b" * 32
        )

        self.assertEqual(result.action, "BLOCK")

    def test_other_generation_blocks(self):
        result = self.decide(
            generation=8
        )

        self.assertEqual(result.action, "BLOCK")

    def test_wrong_job_id_blocks(self):
        result = self.decide(
            job_id="ke24_protected_" + "b" * 32
        )

        self.assertEqual(result.action, "BLOCK")

    def test_missing_journal_reconciles(self):
        self.journal.read.side_effect = (
            FileNotFoundError("Registro inexistente.")
        )

        result = self.decide()

        self.assertEqual(result.action, "RECONCILE")

    def test_invalid_record_reconciles(self):
        self.journal.read.return_value = (
            {
                "run_id": RUN_ID,
                "state": "RUNNING",
                "lock_generation": None,
            },
            2,
        )

        result = self.decide()

        self.assertEqual(result.action, "RECONCILE")

    def test_unknown_journal_can_only_request_review(self):
        self.journal.read.return_value = (
            {
                "run_id": RUN_ID,
                "state": "UNKNOWN",
                "lock_generation": 7,
            },
            2,
        )

        result = self.decide()

        self.assertEqual(
            result.action,
            "REVIEW_REQUIRED",
        )

    def test_invalid_evidence_rejected(self):
        with self.assertRaises(ValueError):
            self.decide(
                job_status="ALMOST_DONE"
            )

        with self.assertRaises(ValueError):
            self.decide(
                manifest_status="LOADED_MAYBE"
            )

        with self.assertRaises(ValueError):
            self.decide(
                lock_status="FREE"
            )

if __name__ == "__main__":
    unittest.main()