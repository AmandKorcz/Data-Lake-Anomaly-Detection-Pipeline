from copy import deepcopy
import json

import pytest

from src.review import ke24_review_trusted_snapshot as snap
from src.review.ke24_review_sheet import SHEET_COLUMNS

KEY = b'x' * 32
ID = 'sheet_example_012345'
ROWS = [list(SHEET_COLUMNS)]


def test_sign_verify_roundtrip():
    doc = snap.signed_document(KEY, ID, 7, ROWS)
    assert snap.verify_document(KEY, doc, ID, 7) == ROWS
    assert 'signature' in doc and KEY.hex() not in str(doc)


def test_tampered_content_is_detected():
    doc = snap.signed_document(KEY, ID, 7, ROWS)
    doc['rows'] = deepcopy(ROWS)
    doc['rows'][0][0] = 'tampered'
    with pytest.raises(ValueError):
        snap.verify_document(KEY, doc, ID, 7)


def test_wrong_id_and_key_fails():
    doc = snap.signed_document(KEY, ID, 7, ROWS)
    with pytest.raises(ValueError):
        snap.verify_document(KEY, doc, 'different_sheet_0123', 7)
    with pytest.raises(ValueError):
        snap.verify_document(b'z' * 32, doc, ID, 7)


def test_missing_local_snapshot_blocks(tmp_path):
    with pytest.raises(RuntimeError, match='ausente'):
        snap.load_snapshot(KEY, ID, 7, path=tmp_path / 'missing.json')


def test_write_read_outside_repository(tmp_path):
    target = tmp_path / 'private' / 'snapshot.json'
    snap.save_snapshot(KEY, ID, 7, ROWS, path=target)
    assert snap.load_snapshot(KEY, ID, 7, path=target) == ROWS
    assert 'signature' in json.loads(target.read_text())
    assert not list(target.parent.glob('*.tmp'))


def test_invalid_signature_is_blocked(tmp_path):
    target = tmp_path / 'snapshot.json'
    target.write_text('{"schema_version": 1, "rows": []}')
    with pytest.raises(RuntimeError):
        snap.load_snapshot(KEY, ID, 7, path=target)


@pytest.mark.parametrize('bad', [None, 'key', b'abc'])
def test_weak_or_invalid_key_blocked(bad):
    with pytest.raises(ValueError):
        snap.signed_document(bad, ID, 7, ROWS)


def test_writer_lock_blocks_nested_publication(tmp_path):
    lock = tmp_path / '.sync.lock'
    with snap.exclusive_review_writer(path=lock):
        with pytest.raises(RuntimeError, match='ativa'):
            with snap.exclusive_review_writer(path=lock):
                pass
        assert lock.exists()
    assert not lock.exists()


def test_modified_writer_lock_not_silently_removed(tmp_path):
    lock = tmp_path / '.sync.lock'
    with pytest.raises(RuntimeError, match='alterado'):
        with snap.exclusive_review_writer(path=lock):
            lock.write_text('another-writer')
    assert lock.exists()
