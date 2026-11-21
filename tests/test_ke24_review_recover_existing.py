from copy import deepcopy

import pytest

from src.review import ke24_review_google_sheets as live
from src.review import ke24_review_trusted_snapshot as snap
from src.review.ke24_review_sheet import SHEET_COLUMNS
from test_ke24_review_google_sheets import FakeDrive, FakeSheets, alerts
from tools.ke24_review_recover_existing import (
    ExistingSheetNotReady, adopt_existing, diagnose_existing,
)

KEY = b"k" * 32
SID = "sheet_example_012345"


def test_google_api_default_false_and_zero_fields_can_be_omitted():
    sheets, drive = FakeSheets(), FakeDrive()
    prot = sheets.metadata["sheets"][0]["protectedRanges"][0]
    del prot["warningOnly"]
    del prot["range"]["startColumnIndex"]
    assert live.verify_private_review_sheet(sheets, drive, SID) == 7


def test_google_api_explicit_warning_true_still_blocked():
    sheets, drive = FakeSheets(), FakeDrive()
    sheets.metadata["sheets"][0]["protectedRanges"][0]["warningOnly"] = True
    with pytest.raises(RuntimeError, match="Protecao"):
        live.verify_private_review_sheet(sheets, drive, SID)


def test_google_api_missing_range_end_is_still_blocked():
    sheets, drive = FakeSheets(), FakeDrive()
    del sheets.metadata["sheets"][0]["protectedRanges"][0]["range"]["endColumnIndex"]
    with pytest.raises(RuntimeError):
        live.verify_private_review_sheet(sheets, drive, SID)


def test_missing_drive_shared_information_still_blocks():
    sheets, drive = FakeSheets(), FakeDrive()
    del drive.target["shared"]
    with pytest.raises(RuntimeError, match="compartilhada"):
        live.verify_private_review_sheet(sheets, drive, SID)


def test_diagnose_uses_no_writes_and_no_private_identifiers(tmp_path):
    sheets, drive = FakeSheets(), FakeDrive()
    data = diagnose_existing(sheets, drive, SID, baseline_file=tmp_path / "snap.json")
    assert data["private_contract"] == "APPROVED"
    assert data["existing_alert_rows"] == 0
    assert data["signed_snapshot_exists"] is False
    assert SID not in str(data)
    assert "owner@example.com" not in str(data)
    assert sheets.appends == 0 and not sheets.batch_bodies


def test_diagnose_reports_warning_field_omitted_without_blocking(tmp_path):
    sheets, drive = FakeSheets(), FakeDrive()
    del sheets.metadata["sheets"][0]["protectedRanges"][0]["warningOnly"]
    data = diagnose_existing(sheets, drive, SID, baseline_file=tmp_path / "snap.json")
    assert data["protection_warningOnly"] == "OMITTED_DEFAULT_FALSE"
    assert data["private_contract"] == "APPROVED"


def test_adopt_creates_only_local_signed_baseline(tmp_path):
    sheets, drive = FakeSheets(), FakeDrive()
    target = tmp_path / "signed.json"
    result = adopt_existing(sheets, drive, SID, KEY, baseline_file=target)
    assert result == "LOCAL_SNAPSHOT_RESTORED"
    assert target.is_file()
    assert snap.load_snapshot(KEY, SID, 7, path=target) == [list(SHEET_COLUMNS)]
    assert sheets.appends == 0 and not sheets.batch_bodies
    assert adopt_existing(sheets, drive, SID, KEY, baseline_file=target) == "ALREADY_INITIALIZED"


def test_adopt_blocks_if_protection_missing(tmp_path):
    sheets, drive = FakeSheets(), FakeDrive()
    sheets.metadata["sheets"][0]["protectedRanges"] = []
    target = tmp_path / "signed.json"
    with pytest.raises(RuntimeError):
        adopt_existing(sheets, drive, SID, KEY, baseline_file=target)
    assert not target.exists()


def test_adopt_blocks_if_shared(tmp_path):
    sheets, drive = FakeSheets(), FakeDrive()
    drive.target["shared"] = True
    target = tmp_path / "signed.json"
    with pytest.raises(RuntimeError):
        adopt_existing(sheets, drive, SID, KEY, baseline_file=target)
    assert not target.exists()


def test_adopt_blocks_if_existing_alert_rows(tmp_path):
    sheets, drive = FakeSheets(), FakeDrive()
    from src.review.ke24_review_sheet import build_review_sheet_matrix
    sheets.matrix = build_review_sheet_matrix(alerts(), [])
    target = tmp_path / "signed.json"
    with pytest.raises(ExistingSheetNotReady, match="vazia"):
        adopt_existing(sheets, drive, SID, KEY, baseline_file=target)
    assert not target.exists()


def test_adopt_does_not_overwrite_invalid_existing_signature(tmp_path):
    sheets, drive = FakeSheets(), FakeDrive()
    target = tmp_path / "signed.json"
    target.write_text('not JSON', encoding='utf-8')
    with pytest.raises(RuntimeError):
        adopt_existing(sheets, drive, SID, KEY, baseline_file=target)
    assert target.read_text(encoding='utf-8') == 'not JSON'


def test_adopt_blocks_change_detected_between_reads(tmp_path):
    sheets, drive = FakeSheets(), FakeDrive()
    target = tmp_path / "signed.json"
    def mutate_after_first_read(s):
        if s.reads == 2:
            s.matrix = [list(SHEET_COLUMNS), ["ALT_" + "a"*32, "2026-10", "PENDING", "PENDING_BUSINESS_RULE", "PENDING", 0, "", "PENDING"]]
    sheets.on_read = mutate_after_first_read
    with pytest.raises(ExistingSheetNotReady, match="alterado"):
        adopt_existing(sheets, drive, SID, KEY, baseline_file=target)
    assert not target.exists()
