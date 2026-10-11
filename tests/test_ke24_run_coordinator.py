import unittest

from src.loading.ke24_run_coordinator import (
    coordinate_execution,
)

class FakeLock:
    #Simula o mecanismo de exclusividade

    def __init__(
        self,
        result=True,
        confirmed=True,
        fail=False,
    ):
        self.result = result
        self.confirmed = confirmed
        self.fail = fail
        self.acquired = False
        self.acquire_calls = 0

    def acquire(self):
        self.acquire_calls += 1

        if self.fail:
            raise RuntimeError(
                "Falha fictícia de comunicação."
            )

        if self.result is True and self.confirmed:
            self.acquired = True

        return self.result

    def release(self):
        raise AssertionError(
            "O coordenador não pode liberar locks."
        )

class TestKE24RunCoordinator(unittest.TestCase):

    def decide(
        self,
        lock,
        policy_status="APPROVED",
        job_status="NEW",
        manifest_status="NEW_FILES",
    ):
        return coordinate_execution(
            policy_status=policy_status,
            job_status=job_status,
            manifest_status=manifest_status,
            execution_lock=lock,
        )

    def test_draft_policy_does_not_acquire_lock(self):
        lock = FakeLock()

        result = self.decide(
            lock,
            policy_status="DRAFT_REVIEW_REQUIRED",
        )

        self.assertEqual(result.action, "BLOCK")
        self.assertEqual(lock.acquire_calls, 0)

    def test_running_job_does_not_acquire_lock(self):
        lock = FakeLock()

        result = self.decide(
            lock,
            job_status="RUNNING",
        )

        self.assertEqual(result.action, "WAIT")
        self.assertEqual(lock.acquire_calls, 0)

    def test_unknown_job_requires_reconciliation(self):
        lock = FakeLock()

        result = self.decide(
            lock,
            job_status="UNKNOWN",
        )

        self.assertEqual(result.action, "RECONCILE")
        self.assertEqual(lock.acquire_calls, 0)

    def test_failed_job_requires_reconciliation(self):
        lock = FakeLock()

        result = self.decide(
            lock,
            job_status="FAILED",
        )

        self.assertEqual(result.action, "RECONCILE")
        self.assertEqual(lock.acquire_calls, 0)

    def test_existing_files_are_skipped(self):
        lock = FakeLock()

        result = self.decide(
            lock,
            manifest_status="ALREADY_LOADED",
        )

        self.assertEqual(result.action, "SKIP")
        self.assertEqual(lock.acquire_calls, 0)

    def test_conflict_blocks_lock_acquisition(self):
        lock = FakeLock()

        result = self.decide(
            lock,
            manifest_status="CONFLICT",
        )

        self.assertEqual(result.action, "BLOCK")
        self.assertEqual(lock.acquire_calls, 0)

    def test_busy_lock_requires_waiting(self):
        lock = FakeLock(result=False)

        result = self.decide(lock)

        self.assertEqual(result.action, "WAIT")
        self.assertEqual(lock.acquire_calls, 1)
        self.assertFalse(lock.acquired)

    def test_acquired_lock_requires_review(self):
        lock = FakeLock()

        result = self.decide(lock)

        self.assertEqual(
            result.action,
            "REVIEW_REQUIRED",
        )

        self.assertEqual(lock.acquire_calls, 1)
        self.assertTrue(lock.acquired)

    def test_uncertain_acquisition_requires_reconciliation(self):
        lock = FakeLock(fail=True)

        result = self.decide(lock)

        self.assertEqual(result.action, "RECONCILE")
        self.assertEqual(lock.acquire_calls, 1)

    def test_unconfirmed_lock_requires_reconciliation(self):
        lock = FakeLock(confirmed=False)

        result = self.decide(lock)

        self.assertEqual(result.action, "RECONCILE")
        self.assertEqual(lock.acquire_calls, 1)

    def test_existing_ownership_is_not_reacquired(self):
        lock = FakeLock()
        lock.acquired = True

        result = self.decide(lock)

        self.assertEqual(result.action, "RECONCILE")
        self.assertEqual(lock.acquire_calls, 0)

if __name__ == "__main__":
    unittest.main()
