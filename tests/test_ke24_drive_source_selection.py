import hashlib
from io import BytesIO

import pandas as pd
import pytest

from src.loading.ke24_drive_source_selection import should_process_source
from src.loading.plan_ke24_incremental import FILE_ID, MANIFEST_COLUMNS

KEY = b"s" * 32


def _hash(buffer):
    position = buffer.tell()
    try:
        buffer.seek(0)
        return hashlib.sha256(buffer.read()).hexdigest()
    finally:
        buffer.seek(position)


def _token(*, value, column, company, secret_key):
    assert column == FILE_ID
    assert company is None
    assert secret_key == KEY
    return "TKN_" + hashlib.sha256(value.encode()).hexdigest()[:32]


def _identifier(content):
    return _token(value=hashlib.sha256(content).hexdigest(),
                  column=FILE_ID, company=None, secret_key=KEY)


def _manifest(*identifiers):
    rows = [{FILE_ID: value} for value in identifiers]
    return pd.DataFrame(rows, columns=MANIFEST_COLUMNS)


def _check(data, manifest, seen):
    return should_process_source(
        BytesIO(data), manifest, seen, KEY,
        hash_function=_hash, token_function=_token,
    )


def test_already_registered_file_is_skipped():
    assert _check(b"already-registered", _manifest(_identifier(b"already-registered")), set()) is False


def test_unregistered_file_is_selected():
    assert _check(b"new-extraction", _manifest(_identifier(b"registered")), set()) is True


def test_initial_load_selects_first_file():
    assert _check(b"first", _manifest(), set()) is True


def test_mixed_sources_select_only_new():
    seen = set()
    loaded = _manifest(_identifier(b"A"), _identifier(b"B"))
    results = [_check(chunk, loaded, seen) for chunk in [b"A", b"C", b"B"]]
    assert results == [False, True, False]
    assert len(seen) == 3


def test_duplicate_raw_bytes_are_blocked_even_when_committed():
    seen = set()
    loaded = _manifest(_identifier(b"same"))
    assert _check(b"same", loaded, seen) is False
    with pytest.raises(ValueError, match="repetido"):
        _check(b"same", loaded, seen)


def test_invalid_manifest_blocks_processing():
    with pytest.raises(ValueError, match="Contrato"):
        _check(b"A", pd.DataFrame({"another_column": []}), set())


def test_manifest_hashes_must_be_protected():
    with pytest.raises(ValueError, match="protegidos"):
        _check(b"A", _manifest("plain-sha256"), set())


def test_duplicate_committed_manifest_is_rejected():
    h = _identifier(b"A")
    with pytest.raises(ValueError, match="duplicados"):
        _check(b"A", _manifest(h, h), set())


def test_short_hmac_key_is_rejected():
    with pytest.raises(ValueError, match="Chave"):
        should_process_source(BytesIO(b"A"), _manifest(), set(), b"short")


def test_buffer_position_is_preserved_for_excel_parser():
    buffer = BytesIO(b"XLSX-simulated")
    buffer.seek(3)
    assert should_process_source(
        buffer, _manifest(), set(), KEY, hash_function=_hash,
        token_function=_token,
    ) is True
    assert buffer.tell() == 3
