"""Testes offline da coleta incremental; nunca usa OAuth, Drive ou publica."""

import hashlib
from io import BytesIO

import pandas as pd
import pytest

from src.loading.ke24_drive_source_selection import collect_new_drive_frames
from src.loading.plan_ke24_incremental import FILE_ID, MANIFEST_COLUMNS

SECRET = b"x" * 32


def _digest_bytes(buffer):
    position = buffer.tell()
    try:
        buffer.seek(0)
        return hashlib.sha256(buffer.read()).hexdigest()
    finally:
        buffer.seek(position)


def _protected_digest(*, value, column, company, secret_key):
    assert column == FILE_ID
    assert company is None
    assert secret_key == SECRET
    return "TKN_" + hashlib.sha256(value.encode()).hexdigest()[:32]


def _protected_id(content):
    return _protected_digest(value=hashlib.sha256(content).hexdigest(),
                             column=FILE_ID, company=None, secret_key=SECRET)


def _manifest(*values):
    return pd.DataFrame([{FILE_ID: v} for v in values], columns=MANIFEST_COLUMNS)


def _fixture(contents, *, corrupt_protection=False):
    calls = {"download": [], "parse": [], "anonymize": [], "validate": []}
    sources = [{"id": f"remote-{i}", "name": f"test-{i}.xlsx"}
               for i in range(len(contents))]

    def download(_service, source_id):
        calls["download"].append(source_id)
        return BytesIO(contents[int(source_id.split("-")[-1])])

    def parse(buffer, *, expected_columns, source_name):
        calls["parse"].append(source_name)
        assert expected_columns == ["period", "sales_quantity"]
        digest = hashlib.sha256(buffer.getvalue()).hexdigest()
        return pd.DataFrame({"raw_digest": [digest]}), None, [], 1

    def anonymize(*, df, policy, schema, secret_key):
        calls["anonymize"].append(True)
        assert secret_key == SECRET
        result = df.copy()
        token = _protected_digest(value=result["raw_digest"].iloc[0],
                                  column=FILE_ID, company=None, secret_key=SECRET)
        result[FILE_ID] = "TKN_" + "f" * 32 if corrupt_protection else token
        return result

    def validate(frame, policy, *, require_all_columns):
        calls["validate"].append(True)
        assert require_all_columns is True
        assert not frame.empty

    return sources, dict(
        expected_columns=["period", "sales_quantity"],
        privacy_policy={}, privacy_schema={},
        download=download, parse=parse, anonymize=anonymize, validate=validate,
        hash_function=_digest_bytes, token_function=_protected_digest,
    ), calls


def test_committed_files_skip_excel_parsing_and_protection():
    contents = [b"registered-A", b"registered-B"]
    files, adapters, calls = _fixture(contents)
    frames, counts = collect_new_drive_frames(
        object(), files, _manifest(*map(_protected_id, contents)), SECRET,
        **adapters,
    )
    assert frames == []
    assert counts == {"raw_files": 2, "new_files": 0, "skipped_files": 2}
    assert len(calls["download"]) == 2  # sha256 precisa dos bytes da API
    assert calls["parse"] == calls["anonymize"] == calls["validate"] == []


def test_mixed_files_parse_only_new_and_validate_protected_hash():
    content = [b"old", b"new-A", b"new-B"]
    files, adapters, calls = _fixture(content)
    frames, counts = collect_new_drive_frames(
        object(), files, _manifest(_protected_id(b"old")), SECRET,
        **adapters,
    )
    assert counts == {"raw_files": 3, "new_files": 2, "skipped_files": 1}
    assert len(frames) == 2
    assert [f[FILE_ID].iloc[0] for f in frames] == [
        _protected_id(b"new-A"), _protected_id(b"new-B")
    ]
    assert calls["parse"] == ["test-1.xlsx", "test-2.xlsx"]
    assert len(calls["anonymize"]) == len(calls["validate"]) == 2


def test_first_load_previews_all_files_without_write():
    files, adapters, calls = _fixture([b"a", b"b"])
    frames, counts = collect_new_drive_frames(
        object(), files, _manifest(), SECRET, **adapters,
    )
    assert len(frames) == 2 and counts["new_files"] == 2
    assert counts["skipped_files"] == 0


def test_duplicate_raw_bytes_stop_even_if_old():
    files, adapters, _ = _fixture([b"same", b"same"])
    with pytest.raises(ValueError, match="repetido"):
        collect_new_drive_frames(object(), files, _manifest(_protected_id(b"same")), SECRET,
                                 **adapters)


def test_unexpected_protected_source_blocks_flow():
    files, adapters, _ = _fixture([b"new"], corrupt_protection=True)
    with pytest.raises(ValueError, match="diverge"):
        collect_new_drive_frames(object(), files, _manifest(), SECRET,
                                 **adapters)


def test_empty_raw_blocks_and_does_not_read():
    _, adapters, calls = _fixture([])
    with pytest.raises(ValueError, match="não possui"):
        collect_new_drive_frames(object(), [], _manifest(), SECRET, **adapters)
    assert calls["download"] == calls["parse"] == []
