import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.privacy import hmac_key


class TestHMACKeyManagement(unittest.TestCase):

    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)

        self.temp_path = Path(self.temp_directory.name)
        self.key_path = self.temp_path / "test_hmac.key"

        self.test_key = bytes.fromhex("ab" * 32)

    def write_key(self, content):
        self.key_path.write_text(
            content,
            encoding="ascii",
        )

    def load_key(self):
        with patch.dict(
            os.environ,
            {"KE24_HMAC_KEY_FILE": str(self.key_path)},
        ):
            return hmac_key.load_hmac_key_from_environment()

    def test_valid_key(self):
        self.write_key(self.test_key.hex())

        loaded_key = self.load_key()

        self.assertEqual(loaded_key, self.test_key)
        self.assertEqual(len(loaded_key), 32)

    def test_same_key_across_executions(self):
        self.write_key(self.test_key.hex())

        first_load = self.load_key()
        second_load = self.load_key()

        self.assertEqual(first_load, second_load)

    def test_missing_environment_variable(self):
        with patch.dict(
            os.environ,
            {"KE24_HMAC_KEY_FILE": ""},
        ):
            with self.assertRaises(RuntimeError):
                hmac_key.load_hmac_key_from_environment()

    def test_missing_key_file(self):
        with self.assertRaises(FileNotFoundError):
            self.load_key()

    def test_invalid_hexadecimal_key(self):
        self.write_key("z" * 64)

        with self.assertRaises(ValueError):
            self.load_key()

    def test_invalid_key_length(self):
        self.write_key("ab" * 16)

        with self.assertRaises(ValueError):
            self.load_key()

    def test_key_inside_repository_rejected(self):
        fake_repository = self.temp_path / "repository"
        fake_repository.mkdir()

        internal_key = fake_repository / "secret.key"
        internal_key.write_text(
            self.test_key.hex(),
            encoding="ascii",
        )

        with (
            patch.object(
                hmac_key,
                "PROJECT_ROOT",
                fake_repository,
            ),
            patch.dict(
                os.environ,
                {"KE24_HMAC_KEY_FILE": str(internal_key)},
            ),
        ):
            with self.assertRaises(ValueError):
                hmac_key.load_hmac_key_from_environment()

    def test_load_key_from_dotenv(self):
        # Cria uma chave fictícia
        self.write_key(self.test_key.hex())

        # Simula a raiz de outro projeto
        fake_repository = self.temp_path / "fake_repository"
        fake_repository.mkdir()

        # Configura apenas o caminho da chave no .env
        env_file = fake_repository / ".env"

        env_file.write_text(
            f"KE24_HMAC_KEY_FILE={self.key_path.as_posix()}\n",
            encoding="utf-8",
        )

        # Remove temporariamente as variáveis do ambiente
        # e força a leitura do .env fictício
        with (
            patch.object(
                hmac_key,
                "PROJECT_ROOT",
                fake_repository,
            ),
            patch.dict(
                os.environ,
                {},
                clear=True,
            ),
        ):
            loaded_key = (
                hmac_key.load_hmac_key_from_environment()
            )

        self.assertEqual(loaded_key, self.test_key)


if __name__ == "__main__":
    unittest.main()
