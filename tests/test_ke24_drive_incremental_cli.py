"""CLI incremental: sem novas extracoes, nao solicita token de escrita."""

import hashlib
from io import BytesIO
import sys

import pandas as pd
import pytest

from src.pipeline import run_ke24_drive_ingestion as runner
from src.loading.plan_ke24_incremental import FILE_ID, MANIFEST_COLUMNS
from src.privacy.anonymize_ke24 import generate_token

KEY = b"a" * 32


def _manifest_for_payload(payload):
    digest = hashlib.sha256(payload).hexdigest()
    token = generate_token(digest, FILE_ID, None, KEY)
    return pd.DataFrame([{FILE_ID: token}], columns=MANIFEST_COLUMNS)


def _no_write_setup(monkeypatch):
    payload = b"xlsx-previously-committed"
    monkeypatch.setattr(runner, "load_privacy_runtime", lambda: ({}, {}, KEY))
    monkeypatch.setattr(runner, "load_schema", lambda: {"columns": ["period"]})
    monkeypatch.setattr(runner, "load_quality_rules", lambda: {})
    monkeypatch.setattr(runner, "authenticate", lambda: object())
    monkeypatch.setattr(runner, "build", lambda *a, **kw: object())
    monkeypatch.setattr(runner, "load_processed_manifest", lambda service: _manifest_for_payload(payload))
    monkeypatch.setattr(runner, "list_raw_xlsx_files", lambda service: [{"id": "remote", "name": "file.xlsx"}])
    monkeypatch.setattr(runner, "read_xlsx_from_drive", lambda service, fid: BytesIO(payload))
    monkeypatch.setattr(runner, "process_ke24_file", lambda *a, **kw: pytest.fail("Ja processado"))
    monkeypatch.setattr(runner, "authenticate_write", lambda: pytest.fail("Escrita nao necessaria"))
    monkeypatch.setattr(runner, "publish_incremental_batch", lambda **kw: pytest.fail("Nao publicar"))


def test_incremental_flag_no_new_files_skips_write_auth(monkeypatch, capsys):
    _no_write_setup(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_ke24_drive_ingestion", "--publish-incremental"])
    runner.main()
    output = capsys.readouterr().out
    assert "Arquivos novos: 0" in output
    assert "Nenhum upload realizado" in output


def test_approved_batch_that_is_no_longer_pending_blocks_publication(monkeypatch):
    _no_write_setup(monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "run_ke24_drive_ingestion", "--publish-incremental", "--expected-batch", "a" * 64,
    ])
    with pytest.raises(RuntimeError, match="Lote aprovado mudou"):
        runner.main()


def test_incremental_flag_rejects_first_publication(monkeypatch):
    monkeypatch.setattr(runner, "load_privacy_runtime", lambda: ({}, {}, KEY))
    monkeypatch.setattr(runner, "load_schema", lambda: {"columns": ["period"]})
    monkeypatch.setattr(runner, "load_quality_rules", lambda: {})
    monkeypatch.setattr(runner, "authenticate", lambda: object())
    monkeypatch.setattr(runner, "build", lambda *a, **kw: object())
    monkeypatch.setattr(runner, "load_processed_manifest", lambda svc: pd.DataFrame(columns=MANIFEST_COLUMNS))
    monkeypatch.setattr(sys, "argv", ["run_ke24_drive_ingestion", "--publish-incremental"])
    with pytest.raises(RuntimeError, match="exige manifesto anterior"):
        runner.main()


def test_publish_flags_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "run_ke24_drive_ingestion", "--publish-initial", "--publish-incremental"
    ])
    with pytest.raises(SystemExit):
        runner.main()
