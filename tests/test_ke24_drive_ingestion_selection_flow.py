"""Verifica que o CLI nao processa nem publica extracoes ja commitadas."""

import hashlib
from io import BytesIO
import sys

import pandas as pd
import pytest

from src.pipeline import run_ke24_drive_ingestion as runner
from src.loading.plan_ke24_incremental import FILE_ID, MANIFEST_COLUMNS
from src.privacy.anonymize_ke24 import generate_token

KEY = b"a" * 32


def _source_token(payload):
    digest = hashlib.sha256(payload).hexdigest()
    return generate_token(digest, FILE_ID, None, KEY)


def _setup_committed(monkeypatch):
    payload = b"already-registered-XLSX"
    manifest = pd.DataFrame([{FILE_ID: _source_token(payload)}], columns=MANIFEST_COLUMNS)
    monkeypatch.setattr(runner, "load_privacy_runtime", lambda: ({}, {}, KEY))
    monkeypatch.setattr(runner, "load_schema", lambda: {"columns": ["period", "sales_quantity"]})
    monkeypatch.setattr(runner, "load_quality_rules", lambda: {})
    monkeypatch.setattr(runner, "authenticate", lambda: object())
    monkeypatch.setattr(runner, "build", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "load_processed_manifest", lambda service: manifest)
    monkeypatch.setattr(runner, "list_raw_xlsx_files", lambda service: [{"id": "id1", "name": "remote.xlsx"}])
    monkeypatch.setattr(runner, "read_xlsx_from_drive", lambda service, file_id: BytesIO(payload))
    monkeypatch.setattr(runner, "process_ke24_file", lambda *args, **kwargs: pytest.fail("Nao reabrir XLSX"))
    monkeypatch.setattr(runner, "anonymize_ke24_dataframe", lambda **kwargs: pytest.fail("Nao reanonimizar"))
    monkeypatch.setattr(runner, "authenticate_write", lambda: pytest.fail("Nao autenticar escrita"))
    monkeypatch.setattr(runner, "publish_initial_batch", lambda **kwargs: pytest.fail("Nao publicar"))


def test_cli_skips_committed_without_excel_or_write(monkeypatch, capsys):
    _setup_committed(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_ke24_drive_ingestion"])
    runner.main()
    output = capsys.readouterr().out
    assert "Arquivos novos: 0" in output
    assert "Arquivos ja processados: 1" in output
    assert "Nenhum arquivo novo" in output


def test_cli_blocks_initial_publish_with_existing_manifest(monkeypatch):
    _setup_committed(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_ke24_drive_ingestion", "--publish-initial"])
    with pytest.raises(RuntimeError, match="--publish-initial"):
        runner.main()
