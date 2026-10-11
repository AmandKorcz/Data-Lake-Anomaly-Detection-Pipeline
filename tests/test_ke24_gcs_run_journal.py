import unittest
from unittest.mock import patch

from google.api_core.exceptions import (
    NotFound,
    PreconditionFailed,
)

from src.loading.gcs_run_journal import GCSRunJournal

RUN_ID = "a" * 32

class FakeStorageState:
    def __init__(self):
        self.objects = {}
        self.counter = 0

class FakeBlob:
    def __init__(self, state, name):
        self.state = state
        self.name = name
        self.generation = None

    def upload_from_string(
        self, data, content_type, if_generation_match
    ):
        current = self.state.objects.get(self.name)

        if if_generation_match == 0:
            if current is not None:
                raise PreconditionFailed("Objeto existente.")
        elif (
            current is None
            or current["generation"] != if_generation_match
        ):
            raise PreconditionFailed("Geração divergente.")

        self.state.counter += 1
        self.generation = self.state.counter

        self.state.objects[self.name] = {
            "generation": self.generation,
            "content": data,
        }

    def reload(self):
        current = self.state.objects.get(self.name)

        if current is None:
            raise NotFound("Objeto inexistente.")

        self.generation = current["generation"]

    def download_as_text(self, if_generation_match):
        current = self.state.objects.get(self.name)

        if (
            current is None
            or current["generation"] != if_generation_match
        ):
            raise PreconditionFailed("Geração divergente.")

        return current["content"]

class FakeBucket:
    def __init__(self, state):
        self.state = state

    def blob(self, name):
        return FakeBlob(self.state, name)

class FakeStorageClient:
    def __init__(self):
        self.state = FakeStorageState()

    def bucket(self, name):
        if name != "test-lock-bucket":
            raise ValueError("Bucket inesperado.")

        return FakeBucket(self.state)

class FakeLock:
    def __init__(
        self,
        run_id=RUN_ID,
        acquired=True,
        generation=10,
    ):
        self.run_id = run_id
        self.acquired = acquired
        self.generation = generation

class TestGCSRunJournal(unittest.TestCase):

    def setUp(self):
        self.client = FakeStorageClient()
        self.journal = self.create_journal()

    def create_journal(self):
        return GCSRunJournal(
            storage_client=self.client,
            bucket_name="test-lock-bucket",
            run_id=RUN_ID,
        )

    def test_creates_running_record(self):
        record = self.journal.start(FakeLock())

        self.assertEqual(record["state"], "RUNNING")
        self.assertEqual(record["run_id"], RUN_ID)
        self.assertEqual(record["lock_generation"], 10)

    def test_record_survives_new_instance(self):
        self.journal.start(FakeLock())

        another_instance = self.create_journal()
        record, generation = another_instance.read()

        self.assertEqual(record["state"], "RUNNING")
        self.assertEqual(generation, 1)

    def test_rejects_missing_lock(self):
        with self.assertRaises(RuntimeError):
            self.journal.start(FakeLock(acquired=False))

    def test_rejects_other_execution_lock(self):
        with self.assertRaises(RuntimeError):
            self.journal.start(FakeLock(run_id="b" * 32))

    def test_rejects_duplicate_registration(self):
        self.journal.start(FakeLock())

        with self.assertRaises(RuntimeError):
            self.create_journal().start(FakeLock())

    def test_marks_unknown(self):
        self.journal.start(FakeLock())

        record = self.journal.mark_unknown()

        self.assertEqual(record["state"], "UNKNOWN")

        persisted, generation = self.journal.read()

        self.assertEqual(persisted["state"], "UNKNOWN")
        self.assertEqual(generation, 2)

    def test_unknown_cannot_be_updated_again(self):
        self.journal.start(FakeLock())
        self.journal.mark_unknown()

        with self.assertRaises(RuntimeError):
            self.journal.mark_unknown()

    def test_missing_record_is_rejected(self):
        with self.assertRaises(FileNotFoundError):
            self.journal.read()

    def test_concurrent_update_is_rejected(self):
        self.journal.start(FakeLock())

        original_upload = self.journal.blob.upload_from_string

        def concurrent_upload(
            data, content_type, if_generation_match
        ):
            # Simula uma alteração concorrente
            stored = self.client.state.objects[
                self.journal.object_name
            ]

            self.client.state.counter += 1

            self.client.state.objects[
                self.journal.object_name
            ] = {
                "generation": self.client.state.counter,
                "content": stored["content"],
            }

            return original_upload(
                data,
                content_type,
                if_generation_match,
            )

        with patch.object(
            self.journal.blob,
            "upload_from_string",
            side_effect=concurrent_upload,
        ):
            with self.assertRaises(RuntimeError):
                self.journal.mark_unknown()

if __name__ == "__main__":
    unittest.main()
