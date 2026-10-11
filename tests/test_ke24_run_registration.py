import unittest

from src.loading.ke24_run_registration import (
    coordinate_and_register,
)

RUN_ID = "a" * 32

class FakeLock:
    def __init__(
        self,
        run_id=RUN_ID,
        available=True,
        fail=False,
        generation=7,
    ):
        self.run_id = run_id
        self.available = available
        self.fail = fail
        self.next_generation = generation

        self.acquired = False
        self.generation = None
        self.acquire_calls = 0

    def acquire(self):
        self.acquire_calls += 1

        if self.fail:
            raise RuntimeError(
                "Falha fictícia na aquisição."
            )

        if not self.available:
            return False

        self.acquired = True
        self.generation = self.next_generation

        return True

    def release(self):
        raise AssertionError(
            "Liberação automática não permitida."
        )

class FakeJournal:
    def __init__(
        self,
        run_id=RUN_ID,
        fail_start=False,
        fail_read=False,
        invalid_record=False,
    ):
        self.run_id = run_id
        self.fail_start = fail_start
        self.fail_read = fail_read
        self.invalid_record = invalid_record

        self.start_calls = 0
        self.read_calls = 0
        self.record = None

    def start(self, execution_lock):
        self.start_calls += 1

        if self.fail_start:
            raise RuntimeError(
                "Falha fictícia ao registrar."
            )

        self.record = {
            "run_id": self.run_id,
            "state": "RUNNING",
            "lock_generation": execution_lock.generation,
        }

        return self.record

    def read(self):
        self.read_calls += 1

        if self.fail_read:
            raise RuntimeError(
                "Falha fictícia na leitura."
            )

        if self.invalid_record:
            return {
                **self.record,
                "lock_generation": 999,
            }, 1

        return self.record.copy(), 1

class TestKE24RunRegistration(unittest.TestCase):

    def setUp(self):
        self.lock = FakeLock()
        self.journal = FakeJournal()

    def execute(
        self,
        policy_status="APPROVED",
        job_status="NEW",
        manifest_status="NEW_FILES",
    ):
        return coordinate_and_register(
            policy_status=policy_status,
            job_status=job_status,
            manifest_status=manifest_status,
            execution_lock=self.lock,
            run_journal=self.journal,
        )

    def test_registers_valid_attempt(self):
        result = self.execute()

        self.assertEqual(
            result.action,
            "REVIEW_REQUIRED",
        )
        self.assertEqual(self.journal.start_calls, 1)
        self.assertEqual(self.journal.read_calls, 1)
        self.assertTrue(self.lock.acquired)

    def test_draft_policy_blocks_registration(self):
        result = self.execute(
            policy_status="DRAFT_REVIEW_REQUIRED"
        )

        self.assertEqual(result.action, "BLOCK")
        self.assertEqual(self.lock.acquire_calls, 0)
        self.assertEqual(self.journal.start_calls, 0)

    def test_running_job_does_not_register(self):
        result = self.execute(
            job_status="RUNNING"
        )

        self.assertEqual(result.action, "WAIT")
        self.assertEqual(self.journal.start_calls, 0)

    def test_unknown_job_requires_reconciliation(self):
        result = self.execute(
            job_status="UNKNOWN"
        )

        self.assertEqual(result.action, "RECONCILE")
        self.assertEqual(self.journal.start_calls, 0)

    def test_existing_files_are_not_registered(self):
        result = self.execute(
            manifest_status="ALREADY_LOADED"
        )

        self.assertEqual(result.action, "SKIP")
        self.assertEqual(self.journal.start_calls, 0)

    def test_conflict_blocks_registration(self):
        result = self.execute(
            manifest_status="CONFLICT"
        )

        self.assertEqual(result.action, "BLOCK")
        self.assertEqual(self.journal.start_calls, 0)

    def test_busy_lock_prevents_registration(self):
        self.lock = FakeLock(available=False)

        result = self.execute()

        self.assertEqual(result.action, "WAIT")
        self.assertEqual(self.journal.start_calls, 0)

    def test_mismatched_run_ids_are_rejected(self):
        self.journal = FakeJournal(run_id="b" * 32)

        with self.assertRaises(ValueError):
            self.execute()

        self.assertEqual(self.lock.acquire_calls, 0)

    def test_journal_failure_keeps_lock(self):
        self.journal = FakeJournal(fail_start=True)

        result = self.execute()

        self.assertEqual(result.action, "RECONCILE")
        self.assertTrue(self.lock.acquired)

    def test_read_failure_keeps_lock(self):
        self.journal = FakeJournal(fail_read=True)

        result = self.execute()

        self.assertEqual(result.action, "RECONCILE")
        self.assertTrue(self.lock.acquired)

    def test_inconsistent_record_requires_reconciliation(self):
        self.journal = FakeJournal(
            invalid_record=True
        )

        result = self.execute()

        self.assertEqual(result.action, "RECONCILE")
        self.assertTrue(self.lock.acquired)

    def test_invalid_lock_generation_is_rejected(self):
        self.lock = FakeLock(generation=None)

        result = self.execute()

        self.assertEqual(result.action, "RECONCILE")
        self.assertEqual(self.journal.start_calls, 0)

if __name__ == "__main__":
    unittest.main()