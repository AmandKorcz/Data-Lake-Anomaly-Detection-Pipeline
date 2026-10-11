#Regressões de segurança do ponto de entrada operacional KE24.

import unittest
from unittest.mock import patch

from src.loading import load_ke24_bigquery as loader
from src.loading.ke24_release_gate import register_protected_run

RUN_ID = "a" * 32
PROTECTED_TABLE_ID = (
    "datalake-pipeline.profitability_data.KE24_staging_protected"
)

class FakeLock:
    def __init__(self, *, available=True, generation=7):
        self.run_id = RUN_ID
        self.available = available
        self.next_generation = generation
        self.acquired = False
        self.generation = None
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire(self):
        self.acquire_calls += 1
        if not self.available:
            return False
        self.acquired = True
        self.generation = self.next_generation
        return True

    def release(self):
        self.release_calls += 1
        raise AssertionError("Nenhuma liberação automática é permitida.")

class FakeJournal:
    def __init__(self, *, fail_start=False):
        self.run_id = RUN_ID
        self.fail_start = fail_start
        self.start_calls = 0
        self.record = None

    def start(self, execution_lock):
        self.start_calls += 1
        if self.fail_start:
            raise RuntimeError("Falha simulada de persistência.")
        self.record = {
            "run_id": self.run_id,
            "state": "RUNNING",
            "lock_generation": execution_lock.generation,
        }
        return self.record

    def read(self):
        if self.record is None:
            raise FileNotFoundError("Sem registro.")
        return self.record.copy(), 1

class TestKE24ReleaseGate(unittest.TestCase):
    def setUp(self):
        self.lock = FakeLock()
        self.journal = FakeJournal()

    def register(self, *, job_status="NEW", manifest_status="NEW_FILES"):
        return register_protected_run(
            job_status=job_status,
            manifest_status=manifest_status,
            execution_lock=self.lock,
            run_journal=self.journal,
        )

    def assert_no_metadata_writes(self):
        self.assertEqual(self.lock.acquire_calls, 0)
        self.assertEqual(self.journal.start_calls, 0)
        self.assertEqual(self.lock.release_calls, 0)

    def test_real_repository_remains_blocked(self):
        # Não substitui o validador: usa configuração real do repositório.
        result = self.register()
        self.assertEqual(result.action, "BLOCK")
        self.assert_no_metadata_writes()

    def test_legacy_destination_blocks_before_policy_validation(self):
        with patch.object(loader, "load_privacy_runtime") as runtime:
            result = self.register()
        self.assertEqual(result.action, "BLOCK")
        runtime.assert_not_called()
        self.assert_no_metadata_writes()

    def test_draft_policy_blocks_even_with_protected_target(self):
        with (
            patch.object(loader, "BQ_TABLE", "KE24_staging_protected"),
            patch.object(loader, "TABLE_ID", PROTECTED_TABLE_ID),
            patch.object(loader, "load_privacy_runtime", side_effect=RuntimeError("Política draft")),
        ):
            result = self.register()
        self.assertEqual(result.action, "BLOCK")
        self.assert_no_metadata_writes()

    def test_missing_key_blocks_even_with_protected_target(self):
        with (
            patch.object(loader, "BQ_TABLE", "KE24_staging_protected"),
            patch.object(loader, "TABLE_ID", PROTECTED_TABLE_ID),
            patch.object(loader, "load_privacy_runtime", side_effect=FileNotFoundError("Chave ausente")),
        ):
            result = self.register()
        self.assertEqual(result.action, "BLOCK")
        self.assert_no_metadata_writes()

    def test_validation_failure_never_leaks_exception(self):
        with patch(
            "src.loading.ke24_release_gate.validate_bigquery_release",
            side_effect=RuntimeError("SEGREDO_FICTICIO_NAO_REVELAR"),
        ):
            result = self.register()
        self.assertEqual(result.action, "BLOCK")
        self.assertNotIn("SEGREDO_FICTICIO", result.reason)
        self.assert_no_metadata_writes()

    def test_caller_cannot_supply_approved_status(self):
        with self.assertRaises(TypeError):
            register_protected_run(
                policy_status="APPROVED",
                job_status="NEW",
                manifest_status="NEW_FILES",
                execution_lock=self.lock,
                run_journal=self.journal,
            )
        self.assert_no_metadata_writes()

    def test_validation_performed_before_lock(self):
        def assert_not_acquired():
            self.assert_no_metadata_writes()

        with patch(
            "src.loading.ke24_release_gate.validate_bigquery_release",
            side_effect=assert_not_acquired,
        ) as validate:
            result = self.register()
        self.assertEqual(result.action, "REVIEW_REQUIRED")
        validate.assert_called_once_with()
        self.assertEqual(self.lock.acquire_calls, 1)
        self.assertEqual(self.journal.start_calls, 1)

    def test_validated_registration_only_requests_review(self):
        with patch("src.loading.ke24_release_gate.validate_bigquery_release"):
            result = self.register()
        self.assertEqual(result.action, "REVIEW_REQUIRED")
        self.assertTrue(self.lock.acquired)
        self.assertEqual(self.journal.start_calls, 1)
        self.assertEqual(self.lock.release_calls, 0)

    def test_busy_lock_waits(self):
        self.lock = FakeLock(available=False)
        with patch("src.loading.ke24_release_gate.validate_bigquery_release"):
            result = self.register()
        self.assertEqual(result.action, "WAIT")
        self.assertEqual(self.journal.start_calls, 0)

    def test_running_job_waits_without_lock(self):
        with patch("src.loading.ke24_release_gate.validate_bigquery_release"):
            result = self.register(job_status="RUNNING")
        self.assertEqual(result.action, "WAIT")
        self.assert_no_metadata_writes()

    def test_unknown_job_reconciles_without_lock(self):
        with patch("src.loading.ke24_release_gate.validate_bigquery_release"):
            result = self.register(job_status="UNKNOWN")
        self.assertEqual(result.action, "RECONCILE")
        self.assert_no_metadata_writes()

    def test_already_loaded_skips_without_lock(self):
        with patch("src.loading.ke24_release_gate.validate_bigquery_release"):
            result = self.register(manifest_status="ALREADY_LOADED")
        self.assertEqual(result.action, "SKIP")
        self.assert_no_metadata_writes()

    def test_conflict_blocks_without_lock(self):
        with patch("src.loading.ke24_release_gate.validate_bigquery_release"):
            result = self.register(manifest_status="CONFLICT")
        self.assertEqual(result.action, "BLOCK")
        self.assert_no_metadata_writes()

    def test_journal_failure_keeps_lock(self):
        self.journal = FakeJournal(fail_start=True)
        with patch("src.loading.ke24_release_gate.validate_bigquery_release"):
            result = self.register()
        self.assertEqual(result.action, "RECONCILE")
        self.assertTrue(self.lock.acquired)
        self.assertEqual(self.lock.release_calls, 0)

    def test_second_attempt_does_not_acquire_again(self):
        with patch("src.loading.ke24_release_gate.validate_bigquery_release"):
            self.assertEqual(self.register().action, "REVIEW_REQUIRED")
            result = self.register()
        self.assertEqual(result.action, "RECONCILE")
        self.assertEqual(self.lock.acquire_calls, 1)
        self.assertEqual(self.journal.start_calls, 1)

    def test_invalid_generation_requires_reconciliation(self):
        self.lock = FakeLock(generation=None)
        with patch("src.loading.ke24_release_gate.validate_bigquery_release"):
            result = self.register()
        self.assertEqual(result.action, "RECONCILE")
        self.assertEqual(self.journal.start_calls, 0)
        self.assertEqual(self.lock.release_calls, 0)


if __name__ == "__main__":
    unittest.main()
