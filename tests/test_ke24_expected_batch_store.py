import json
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from google.api_core.exceptions import NotFound, PreconditionFailed

from src.loading.ke24_expected_batch_store import (
    GCSExpectedBatchStore,
    reconcile_persisted_batch,
)
from src.loading.ke24_execution_control import ExecutionDecision

RUN_ID = "a" * 32

def fake_file(letter, period):
    return {
        "_source_file_sha256": "TKN_" + letter * 32,
        "company_code": "TKN_" + "c" * 32,
        "period": period,
        "period_year": "2026",
        "currency": "RUB",
        "record_count": 2,
    }

class FakeBlob:
    def __init__(self, state, name):
        self.state = state
        self.name = name
        self.generation = None

    def upload_from_string(self, data, content_type, if_generation_match):
        if if_generation_match != 0:
            raise AssertionError("É proibido sobrescrever o manifesto.")
        if self.name in self.state["objects"]:
            raise PreconditionFailed("Já existe.")
        self.state["counter"] += 1
        self.generation = self.state["counter"]
        self.state["objects"][self.name] = {
            "generation": self.generation,
            "content": data,
        }

    def reload(self):
        obj = self.state["objects"].get(self.name)
        if obj is None:
            raise NotFound("Não encontrado.")
        self.generation = obj["generation"]

    def download_as_text(self, if_generation_match):
        obj = self.state["objects"].get(self.name)
        if obj is None or obj["generation"] != if_generation_match:
            raise PreconditionFailed("Geração alterada.")
        return obj["content"]

class FakeStorageClient:
    def __init__(self):
        self.state = {"objects": {}, "counter": 0}
        self.bucket_names = []

    def bucket(self, name):
        self.bucket_names.append(name)
        return self

    def blob(self, name):
        return FakeBlob(self.state, name)

class FakeJournal:
    def __init__(self, run_id=RUN_ID, state="RUNNING", lock_generation=7):
        self.run_id = run_id
        self.state = state
        self.lock_generation = lock_generation

    def read(self):
        return {
            "run_id": self.run_id,
            "state": self.state,
            "lock_generation": self.lock_generation,
        }, 1

class FakeLock:
    def __init__(self, run_id=RUN_ID, acquired=True, generation=7):
        self.run_id = run_id
        self.acquired = acquired
        self.generation = generation

class TestKE24ExpectedBatchStore(unittest.TestCase):
    def setUp(self):
        self.client = FakeStorageClient()
        self.journal = FakeJournal()
        self.lock = FakeLock()
        self.expected = pd.DataFrame([fake_file("a", 1), fake_file("b", 2)])
        self.store = self.new_store()

    def new_store(self):
        return GCSExpectedBatchStore(self.client, "fake-lock-bucket", RUN_ID)

    def save(self):
        return self.store.save(
            expected_manifest=self.expected,
            run_journal=self.journal,
            execution_lock=self.lock,
        )

    def test_persist_and_recover_in_new_instance(self):
        saved = self.save()
        recovered = self.new_store().read(run_journal=self.journal)
        self.assertEqual(len(saved), 2)
        self.assertEqual(len(recovered), 2)
        self.assertEqual(set(recovered["period"]), {1, 2})

    def test_does_not_persist_financial_values_or_original_filename(self):
        self.expected["revenue"] = 123456
        self.expected["_source_file"] = "PRIVATE_FILENAME.xlsx"
        self.save()
        raw = self.client.state["objects"][self.store.object_name]["content"]
        self.assertNotIn("revenue", raw)
        self.assertNotIn("PRIVATE_FILENAME", raw)
        self.assertNotIn("_source_file\"", raw)
        self.assertNotIn("123456", raw)

    def test_duplicate_write_is_blocked(self):
        self.save()
        with self.assertRaises(RuntimeError):
            self.new_store().save(
                expected_manifest=self.expected,
                run_journal=self.journal,
                execution_lock=self.lock,
            )

    def test_rejects_invalid_expected(self):
        self.expected.loc[0, "company_code"] = "COMPANY_REAL"
        with self.assertRaises(ValueError):
            self.save()
        self.assertEqual(self.client.state["objects"], {})

    def test_rejects_wrong_run_id(self):
        self.lock.run_id = "b" * 32
        with self.assertRaises(RuntimeError):
            self.save()

    def test_rejects_unconfirmed_lock(self):
        self.lock.acquired = False
        with self.assertRaises(RuntimeError):
            self.save()

    def test_rejects_wrong_lock_generation(self):
        self.lock.generation = 8
        with self.assertRaises(RuntimeError):
            self.save()

    def test_rejects_wrong_journal(self):
        self.journal.run_id = "b" * 32
        with self.assertRaises(ValueError):
            self.save()

    def test_read_missing_record_is_not_found(self):
        with self.assertRaises(FileNotFoundError):
            self.store.read(run_journal=self.journal)

    def test_tampered_digest_is_rejected(self):
        self.save()
        obj = self.client.state["objects"][self.store.object_name]
        payload = json.loads(obj["content"])
        payload["files"][0]["record_count"] = 999
        obj["content"] = json.dumps(payload)
        with self.assertRaises(ValueError):
            self.store.read(run_journal=self.journal)

    def test_changed_generation_is_rejected(self):
        self.save()
        with patch.object(
            self.store.blob,
            "download_as_text",
            side_effect=PreconditionFailed("changed"),
        ):
            with self.assertRaises(RuntimeError):
                self.store.read(run_journal=self.journal)

    def test_unknown_journal_can_recover_manifest(self):
        self.save()
        self.journal.state = "UNKNOWN"
        recovered = self.new_store().read(run_journal=self.journal)
        self.assertEqual(len(recovered), 2)

    def test_unknown_journal_cannot_create_new_manifest(self):
        self.journal.state = "UNKNOWN"
        with self.assertRaises(RuntimeError):
            self.save()

    def test_extra_manifest_key_is_rejected(self):
        self.save()
        obj = self.client.state["objects"][self.store.object_name]
        payload = json.loads(obj["content"])
        payload["files"][0]["customer"] = "TKN_" + "d" * 32
        obj["content"] = json.dumps(payload)
        with self.assertRaises(ValueError):
            self.store.read(run_journal=self.journal)

    def test_reconcile_restores_exact_expected_manifest(self):
        self.save()
        remote = self.expected.copy()
        with patch(
            "src.loading.ke24_expected_batch_store.reconcile_observed_batch",
            return_value=ExecutionDecision("REVIEW_REQUIRED", "Revisão pendente."),
        ) as delegate:
            result = reconcile_persisted_batch(
                batch_store=self.new_store(),
                run_journal=self.journal,
                storage_client=self.client,
                bucket_name="fake-lock-bucket",
                bigquery_client=Mock(),
                remote_manifest=remote,
            )
        self.assertEqual(result.action, "REVIEW_REQUIRED")
        observed = delegate.call_args.kwargs["expected_manifest"]
        self.assertEqual(len(observed), 2)
        self.assertEqual(set(observed["period"]), {1, 2})

    def test_missing_expected_prevents_observers(self):
        with patch(
            "src.loading.ke24_expected_batch_store.reconcile_observed_batch"
        ) as delegate:
            result = reconcile_persisted_batch(
                batch_store=self.store,
                run_journal=self.journal,
                storage_client=self.client,
                bucket_name="fake-lock-bucket",
                bigquery_client=Mock(),
                remote_manifest=self.expected,
            )
        self.assertEqual(result.action, "RECONCILE")
        delegate.assert_not_called()


    def test_altered_run_identity_is_rejected(self):
        self.save()
        obj = self.client.state["objects"][self.store.object_name]
        payload = json.loads(obj["content"])
        payload["run_id"] = "b" * 32
        obj["content"] = json.dumps(payload)
        with self.assertRaises(ValueError):
            self.store.read(run_journal=self.journal)

    def test_journal_lock_generation_change_is_rejected(self):
        self.save()
        self.journal.lock_generation = 8
        with self.assertRaises(ValueError):
            self.store.read(run_journal=self.journal)

    def test_full_reconciliation_with_matching_batch(self):
        from src.loading.ke24_run_reconciliation import (
            JobObservation, LockObservation, expected_job_id,
        )
        self.save()
        with (
            patch(
                "src.loading.ke24_batch_reconciliation.observe_gcs_lock",
                return_value=LockObservation("PRESENT", RUN_ID, 7),
            ),
            patch(
                "src.loading.ke24_batch_reconciliation.observe_bigquery_job",
                return_value=JobObservation("SUCCEEDED", expected_job_id(RUN_ID)),
            ),
        ):
            result = reconcile_persisted_batch(
                batch_store=self.new_store(), run_journal=self.journal,
                storage_client=self.client, bucket_name="fake-lock-bucket",
                bigquery_client=Mock(), remote_manifest=self.expected.copy(),
            )
        self.assertEqual(result.action, "REVIEW_REQUIRED")

    def test_full_reconciliation_with_missing_file(self):
        from src.loading.ke24_run_reconciliation import (
            JobObservation, LockObservation, expected_job_id,
        )
        self.save()
        with (
            patch(
                "src.loading.ke24_batch_reconciliation.observe_gcs_lock",
                return_value=LockObservation("PRESENT", RUN_ID, 7),
            ),
            patch(
                "src.loading.ke24_batch_reconciliation.observe_bigquery_job",
                return_value=JobObservation("SUCCEEDED", expected_job_id(RUN_ID)),
            ),
        ):
            result = reconcile_persisted_batch(
                batch_store=self.new_store(), run_journal=self.journal,
                storage_client=self.client, bucket_name="fake-lock-bucket",
                bigquery_client=Mock(), remote_manifest=self.expected.iloc[:1].copy(),
            )
        self.assertEqual(result.action, "RECONCILE")


if __name__ == "__main__":
    unittest.main()