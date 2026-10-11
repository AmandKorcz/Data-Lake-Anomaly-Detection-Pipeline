import unittest

from src.loading.ke24_execution_control import (
    decide_execution,
)

class TestKE24ExecutionControl(unittest.TestCase):

    def decide(
        self,
        policy_status="APPROVED",
        job_status="NEW",
        manifest_status="NEW_FILES",
        exclusive_lock_acquired=True,
    ):
        return decide_execution(
            policy_status=policy_status,
            job_status=job_status,
            manifest_status=manifest_status,
            exclusive_lock_acquired=exclusive_lock_acquired,
        )

    def test_draft_policy_blocks_execution(self):
        result = self.decide(
            policy_status="DRAFT_REVIEW_REQUIRED"
        )

        self.assertEqual(result.action, "BLOCK")

    def test_running_job_must_wait(self):
        result = self.decide(
            job_status="RUNNING"
        )

        self.assertEqual(result.action, "WAIT")

    def test_failed_job_requires_reconciliation(self):
        result = self.decide(
            job_status="FAILED"
        )

        self.assertEqual(result.action, "RECONCILE")

    def test_unknown_job_requires_reconciliation(self):
        result = self.decide(
            job_status="UNKNOWN"
        )

        self.assertEqual(result.action, "RECONCILE")

    def test_unverified_manifest_requires_reconciliation(self):
        result = self.decide(
            manifest_status="UNVERIFIED"
        )

        self.assertEqual(result.action, "RECONCILE")

    def test_existing_files_are_skipped(self):
        result = self.decide(
            manifest_status="ALREADY_LOADED"
        )

        self.assertEqual(result.action, "SKIP")

    def test_conflict_blocks_execution(self):
        result = self.decide(
            manifest_status="CONFLICT"
        )

        self.assertEqual(result.action, "BLOCK")

    def test_missing_exclusive_lock_must_wait(self):
        result = self.decide(
            exclusive_lock_acquired=False
        )

        self.assertEqual(result.action, "WAIT")

    def test_new_job_requires_operational_review(self):
        result = self.decide()

        self.assertEqual(
            result.action,
            "REVIEW_REQUIRED"
        )

    def test_completed_job_with_missing_data_blocks(self):
        result = self.decide(
            job_status="SUCCEEDED",
            manifest_status="NEW_FILES",
        )

        self.assertEqual(result.action, "BLOCK")

    def test_invalid_execution_state_is_rejected(self):
        with self.assertRaises(ValueError):
            self.decide(
                job_status="INVALID_STATE"
            )

if __name__ == "__main__":
    unittest.main()
