#Integração offline da trilha protegida KE24.

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from google.api_core.exceptions import NotFound, PreconditionFailed

from src.loading.gcs_execution_lock import (
    GCSExecutionLock,
    LOCK_OBJECT_NAME,
)
from src.loading.gcs_run_journal import GCSRunJournal
from src.loading.ke24_expected_batch_store import (
    GCSExpectedBatchStore,
    reconcile_persisted_batch,
)
from src.loading.ke24_run_reconciliation import expected_job_id
from src.loading.ke24_run_registration import coordinate_and_register
from src.loading.ke24_state_observers import BQ_LOCATION, BQ_PROJECT


RUN_ID = "a" * 32
OTHER_RUN_ID = "b" * 32
BUCKET_NAME = "offline-ke24-tests"


def fake_file(letter, period, count=2):
    return {
        "_source_file_sha256": "TKN_" + letter * 32,
        "company_code": "TKN_" + "c" * 32,
        "period": period,
        "period_year": "2026",
        "currency": "RUB",
        "record_count": count,
    }


class FakeBlob:
    #GCS em memória, incluindo precondições de geração

    def __init__(self, storage, name):
        self.storage = storage
        self.name = name
        self.generation = None

    def upload_from_string(self, data, content_type, if_generation_match):
        self.storage.upload_calls += 1
        current = self.storage.objects.get(self.name)
        if if_generation_match == 0:
            if current is not None:
                raise PreconditionFailed("Objeto já existe")
        elif (
            current is None
            or current["generation"] != if_generation_match
        ):
            raise PreconditionFailed("Geração divergente")

        self.storage.counter += 1
        self.generation = self.storage.counter
        self.storage.objects[self.name] = {
            "generation": self.generation,
            "content": data,
            "content_type": content_type,
        }

    def reload(self):
        current = self.storage.objects.get(self.name)
        if current is None:
            raise NotFound("Objeto inexistente")
        self.generation = current["generation"]

    def download_as_text(self, if_generation_match):
        current = self.storage.objects.get(self.name)
        if (
            current is None
            or current["generation"] != if_generation_match
        ):
            raise PreconditionFailed("Geração alterada")
        return current["content"]

    def delete(self, if_generation_match):
        self.storage.delete_calls += 1
        current = self.storage.objects.get(self.name)
        if (
            current is None
            or current["generation"] != if_generation_match
        ):
            raise PreconditionFailed("Geração incorreta")
        del self.storage.objects[self.name]


class FakeBucket:
    def __init__(self, storage):
        self.storage = storage

    def blob(self, name):
        return FakeBlob(self.storage, name)


class FakeStorageClient:
    def __init__(self):
        self.objects = {}
        self.counter = 0
        self.upload_calls = 0
        self.delete_calls = 0

    def bucket(self, name):
        if name != BUCKET_NAME:
            raise AssertionError("Bucket não autorizado na simulação")
        return FakeBucket(self)


class FakeBigQueryClient:
    #Só oferece consulta de status: qualquer tentativa de carga falha

    def __init__(self):
        self.job_state = "DONE"
        self.job_error = None
        self.job_type = "query"
        self.job_location = BQ_LOCATION
        self.job_id = expected_job_id(RUN_ID)
        self.get_job_error = None
        self.get_job_calls = 0
        self.write_calls = 0

    def get_job(self, job_id, *, project, location):
        self.get_job_calls += 1
        if project != BQ_PROJECT or location != BQ_LOCATION:
            raise AssertionError("Projeto ou região divergentes")
        if self.get_job_error is not None:
            raise self.get_job_error
        return SimpleNamespace(
            job_id=self.job_id,
            project=BQ_PROJECT,
            location=self.job_location,
            job_type=self.job_type,
            state=self.job_state,
            error_result=self.job_error,
        )

    def query(self, *args, **kwargs):
        self.write_calls += 1
        raise AssertionError("SQL proibido no teste offline")

    def load_table_from_dataframe(self, *args, **kwargs):
        self.write_calls += 1
        raise AssertionError("Carga BigQuery proibida no teste offline")

    def load_table_from_file(self, *args, **kwargs):
        self.write_calls += 1
        raise AssertionError("Carga BigQuery proibida no teste offline")


class TestKE24OfflineSecurityIntegration(unittest.TestCase):
    def setUp(self):
        self.storage = FakeStorageClient()
        self.bigquery = FakeBigQueryClient()
        self.expected = pd.DataFrame([
            fake_file("a", 1),
            fake_file("b", 2),
        ])
        self.lock = GCSExecutionLock(
            self.storage, BUCKET_NAME, run_id=RUN_ID
        )
        self.journal = GCSRunJournal(
            self.storage, BUCKET_NAME, run_id=RUN_ID
        )
        self.batch_store = GCSExpectedBatchStore(
            self.storage, BUCKET_NAME, run_id=RUN_ID
        )

    def register(self, policy_status="APPROVED"):
        # APPROVED é apenas uma entrada sintética para exercitar a lógica.
        # A política real do repositório NÃO é alterada.
        return coordinate_and_register(
            policy_status=policy_status,
            job_status="NEW",
            manifest_status="NEW_FILES",
            execution_lock=self.lock,
            run_journal=self.journal,
        )

    def prepare(self):
        self.assertEqual(self.register().action, "REVIEW_REQUIRED")
        saved = self.batch_store.save(
            expected_manifest=self.expected,
            run_journal=self.journal,
            execution_lock=self.lock,
        )
        self.assertEqual(len(saved), 2)

    def reconcile(self, remote=None, *, new_instances=False):
        if remote is None:
            remote = self.expected.copy()
        journal = self.journal
        store = self.batch_store
        if new_instances:
            journal = GCSRunJournal(self.storage, BUCKET_NAME, RUN_ID)
            store = GCSExpectedBatchStore(self.storage, BUCKET_NAME, RUN_ID)
        return reconcile_persisted_batch(
            batch_store=store,
            run_journal=journal,
            storage_client=self.storage,
            bucket_name=BUCKET_NAME,
            bigquery_client=self.bigquery,
            remote_manifest=remote,
        )

    def assert_no_real_writes(self):
        self.assertEqual(self.bigquery.write_calls, 0)
        self.assertEqual(self.storage.delete_calls, 0)

    def test_real_policy_is_still_draft(self):
        policy_file = (
            Path(__file__).resolve().parents[1]
            / "config" / "ke24_privacy_policy.json"
        )
        policy = json.loads(policy_file.read_text(encoding="utf-8"))
        self.assertEqual(policy["status"], "DRAFT_REVIEW_REQUIRED")

    def test_draft_blocks_before_creating_lock_or_journal(self):
        result = self.register("DRAFT_REVIEW_REQUIRED")
        self.assertEqual(result.action, "BLOCK")
        self.assertEqual(self.storage.objects, {})
        self.assertEqual(self.storage.upload_calls, 0)
        self.assert_no_real_writes()

    def test_registration_persists_lock_and_journal(self):
        result = self.register()
        self.assertEqual(result.action, "REVIEW_REQUIRED")
        self.assertIn(LOCK_OBJECT_NAME, self.storage.objects)
        record, _ = self.journal.read()
        self.assertEqual(record["lock_generation"], self.lock.generation)
        self.assert_no_real_writes()

    def test_expected_manifest_persists_separately(self):
        self.prepare()
        self.assertIn(self.batch_store.object_name, self.storage.objects)
        self.assertEqual(len(self.batch_store.read(run_journal=self.journal)), 2)
        self.assert_no_real_writes()

    def test_success_is_only_a_candidate_for_review(self):
        self.prepare()
        result = self.reconcile()
        self.assertEqual(result.action, "REVIEW_REQUIRED")
        self.assert_no_real_writes()

    def test_restart_recovers_expected_manifest_and_reconciles(self):
        self.prepare()
        result = self.reconcile(new_instances=True)
        self.assertEqual(result.action, "REVIEW_REQUIRED")
        self.assert_no_real_writes()

    def test_missing_expected_file_needs_reconciliation(self):
        self.prepare()
        result = self.reconcile(self.expected.iloc[:1].copy())
        self.assertEqual(result.action, "RECONCILE")
        self.assert_no_real_writes()

    def test_conflicting_record_count_blocks(self):
        self.prepare()
        remote = self.expected.copy()
        remote.loc[0, "record_count"] = 3
        self.assertEqual(self.reconcile(remote).action, "BLOCK")
        self.assert_no_real_writes()

    def test_replaced_month_blocks(self):
        self.prepare()
        remote = self.expected.copy()
        remote.loc[0, "_source_file_sha256"] = "TKN_" + "d" * 32
        self.assertEqual(self.reconcile(remote).action, "BLOCK")
        self.assert_no_real_writes()

    def test_unrelated_extra_month_does_not_change_expected_batch(self):
        self.prepare()
        remote = pd.concat([
            self.expected,
            pd.DataFrame([fake_file("d", 3)]),
        ], ignore_index=True)
        self.assertEqual(self.reconcile(remote).action, "REVIEW_REQUIRED")
        self.assert_no_real_writes()

    def test_simultaneous_attempt_must_wait(self):
        self.assertEqual(self.register().action, "REVIEW_REQUIRED")
        competing_lock = GCSExecutionLock(
            self.storage, BUCKET_NAME, run_id=OTHER_RUN_ID
        )
        competing_journal = GCSRunJournal(
            self.storage, BUCKET_NAME, run_id=OTHER_RUN_ID
        )
        result = coordinate_and_register(
            policy_status="APPROVED", job_status="NEW",
            manifest_status="NEW_FILES",
            execution_lock=competing_lock,
            run_journal=competing_journal,
        )
        self.assertEqual(result.action, "WAIT")
        self.assertFalse(competing_lock.acquired)
        self.assert_no_real_writes()

    def test_running_job_must_wait(self):
        self.prepare()
        self.bigquery.job_state = "RUNNING"
        self.assertEqual(self.reconcile().action, "WAIT")
        self.assert_no_real_writes()

    def test_failed_job_needs_reconciliation(self):
        self.prepare()
        self.bigquery.job_error = {"reason": "fakeError"}
        self.assertEqual(self.reconcile().action, "RECONCILE")
        self.assert_no_real_writes()

    def test_job_not_found_needs_reconciliation(self):
        self.prepare()
        self.bigquery.get_job_error = NotFound("Job simulado ausente")
        self.assertEqual(self.reconcile().action, "RECONCILE")
        self.assert_no_real_writes()

    def test_job_timeout_needs_reconciliation(self):
        self.prepare()
        self.bigquery.get_job_error = TimeoutError("Falha simulada")
        self.assertEqual(self.reconcile().action, "RECONCILE")
        self.assert_no_real_writes()

    def test_wrong_job_location_needs_reconciliation(self):
        self.prepare()
        self.bigquery.job_location = "us"
        self.assertEqual(self.reconcile().action, "RECONCILE")
        self.assert_no_real_writes()

    def test_missing_lock_needs_reconciliation(self):
        self.prepare()
        del self.storage.objects[LOCK_OBJECT_NAME]  # Apenas simulação
        self.assertEqual(self.reconcile().action, "RECONCILE")
        self.assert_no_real_writes()

    def test_replaced_lock_owner_blocks(self):
        self.prepare()
        self.storage.objects[LOCK_OBJECT_NAME]["content"] = OTHER_RUN_ID
        self.assertEqual(self.reconcile().action, "BLOCK")
        self.assert_no_real_writes()

    def test_missing_manifest_never_calls_bigquery(self):
        self.assertEqual(self.register().action, "REVIEW_REQUIRED")
        self.assertEqual(self.reconcile().action, "RECONCILE")
        self.assertEqual(self.bigquery.get_job_calls, 0)
        self.assert_no_real_writes()

    def test_fake_financial_data_is_not_saved_in_manifest(self):
        self.expected["revenue"] = 123456789
        self.expected["_source_file"] = "FICTITIOUS_PRIVATE_NAME.xlsx"
        self.prepare()
        content = self.storage.objects[self.batch_store.object_name]["content"]
        self.assertNotIn("123456789", content)
        self.assertNotIn("FICTITIOUS_PRIVATE_NAME", content)
        self.assertNotIn('"revenue"', content)
        self.assert_no_real_writes()


    def test_tampered_persisted_manifest_requires_reconciliation(self):
        self.prepare()
        obj = self.storage.objects[self.batch_store.object_name]
        payload = json.loads(obj["content"])
        payload["files"][0]["record_count"] = 999
        obj["content"] = json.dumps(payload)
        self.assertEqual(self.reconcile().action, "RECONCILE")
        self.assertEqual(self.bigquery.get_job_calls, 0)
        self.assert_no_real_writes()

    def test_unknown_journal_keeps_lock_even_after_review(self):
        self.prepare()
        self.journal.mark_unknown()
        self.assertEqual(self.reconcile().action, "REVIEW_REQUIRED")
        self.assertIn(LOCK_OBJECT_NAME, self.storage.objects)
        self.assert_no_real_writes()


if __name__ == "__main__":
    unittest.main()
