import unittest

from google.api_core.exceptions import PreconditionFailed

from src.loading.gcs_execution_lock import (
    GCSExecutionLock,
    LOCK_OBJECT_NAME,
)

class FakeStorageState:
    def __init__(self):
        self.current = None
        self.counter = 0

class FakeBlob:
    def __init__(self, state):
        self.state = state
        self.generation = None

    def upload_from_string(
        self,
        data,
        content_type,
        if_generation_match,
    ):
        if if_generation_match != 0:
            raise AssertionError(
                "Criação sem condição de exclusividade."
            )

        if self.state.current is not None:
            raise PreconditionFailed("Lock já existente.")

        self.state.counter += 1

        self.generation = self.state.counter

        self.state.current = {
            "generation": self.generation,
            "owner": data,
        }

    def delete(self, if_generation_match):
        current = self.state.current

        if (
            current is None
            or current["generation"] != if_generation_match
        ):
            raise PreconditionFailed(
                "A geração do lock não corresponde."
            )

        self.state.current = None

class FakeBucket:
    def __init__(self, state):
        self.state = state

    def blob(self, name):
        if name != LOCK_OBJECT_NAME:
            raise AssertionError(
                "Nome do objeto de lock incorreto."
            )

        return FakeBlob(self.state)

class FakeStorageClient:
    def __init__(self):
        self.state = FakeStorageState()

    def bucket(self, name):
        if name != "test-lock-bucket":
            raise AssertionError(
                "Bucket de teste incorreto."
            )

        return FakeBucket(self.state)

class TestGCSExecutionLock(unittest.TestCase):

    def setUp(self):
        self.client = FakeStorageClient()

    def create_lock(self):
        return GCSExecutionLock(
            storage_client=self.client,
            bucket_name="test-lock-bucket",
        )

    def test_first_execution_acquires_lock(self):
        lock = self.create_lock()

        self.assertTrue(lock.acquire())
        self.assertTrue(lock.acquired)
        self.assertEqual(lock.generation, 1)

    def test_second_execution_must_wait(self):
        first = self.create_lock()
        second = self.create_lock()

        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())

        self.assertFalse(second.acquired)

    def test_release_allows_new_execution(self):
        first = self.create_lock()
        second = self.create_lock()

        self.assertTrue(first.acquire())

        first.release()

        self.assertFalse(first.acquired)
        self.assertTrue(second.acquire())

        self.assertEqual(second.generation, 2)

    def test_release_without_ownership_is_rejected(self):
        lock = self.create_lock()

        with self.assertRaises(RuntimeError):
            lock.release()

    def test_stale_owner_cannot_delete_new_lock(self):
        first = self.create_lock()

        self.assertTrue(first.acquire())

        # Simula remoção externa do objeto. A primeira execução ainda acredita possuir a geração antiga.
        self.client.state.current = None

        second = self.create_lock()

        self.assertTrue(second.acquire())

        with self.assertRaises(RuntimeError):
            first.release()

        self.assertTrue(second.acquired)

        self.assertEqual(
            self.client.state.current["generation"],
            second.generation,
        )

    def test_crashed_execution_keeps_lock(self):
        first = self.create_lock()

        self.assertTrue(first.acquire())

        # Simula uma execução que desapareceu sem liberar o lock.
        del first

        second = self.create_lock()

        self.assertFalse(second.acquire())
        self.assertFalse(second.acquired)

    def test_same_execution_cannot_acquire_twice(self):
        lock = self.create_lock()

        self.assertTrue(lock.acquire())

        with self.assertRaises(RuntimeError):
            lock.acquire()


if __name__ == "__main__":
    unittest.main()
