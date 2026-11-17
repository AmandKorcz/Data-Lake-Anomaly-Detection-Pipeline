"""Auditoria e concorrencia locais; os testes nunca conectam ao Google Drive."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.pipeline import run_ke24
from src.pipeline.ke24_preview_execution_guard import (
    PreviewAlreadyRunning,
    exclusive_preview,
    read_preview_report,
    report_file,
    write_preview_report,
)


def _fake_project(root: Path) -> Path:
    for _label, filename in run_ke24.STEPS:
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")
    for filename in run_ke24.PREVIEW_FILES:
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")
    return root


def _env():
    return {
        "KE24_DRIVE_RAW_FOLDER_ID": "raw-folder",
        "KE24_DRIVE_PROCESSED_FOLDER_ID": "processed-folder",
        "KE24_DRIVE_ALERTS_FOLDER_ID": "alerts-folder",
    }


def test_exclusive_preview_rejects_second_process_and_cleans_up(tmp_path):
    lock = tmp_path / "data/curated/.ke24_preview.lock"
    with exclusive_preview(tmp_path, "first"):
        assert lock.is_file()
        with pytest.raises(PreviewAlreadyRunning):
            with exclusive_preview(tmp_path, "second"):
                pytest.fail("Duplicated process admitted")
    assert not lock.exists()


def test_exclusive_preview_cleans_up_after_error(tmp_path):
    lock = tmp_path / "data/curated/.ke24_preview.lock"
    with pytest.raises(ValueError):
        with exclusive_preview(tmp_path, "first"):
            raise ValueError("test interruption")
    assert not lock.exists()


def test_report_stores_only_safe_aggregate_metrics(tmp_path):
    write_preview_report(
        tmp_path, run_id="abcd1234", started_at=datetime.now(timezone.utc),
        duration_seconds=1.5, status="SUCCESS", result={
            "candidate_count": 83,
            "secret_key": "SHOULD_NOT_APPEAR",
            "raw_file_id": "SHOULD_NOT_APPEAR",
            "processed_rows": 2105,
            "history_new": -1,
        },
    )
    raw = report_file(tmp_path).read_text(encoding="utf-8")
    result = read_preview_report(tmp_path)
    assert "SHOULD_NOT_APPEAR" not in raw
    assert result["metrics"] == {"processed_rows": 2105, "candidate_count": 83}
    assert result["status"] == "SUCCESS"


def test_preview_success_records_status_and_releases_lock(tmp_path, monkeypatch):
    _fake_project(tmp_path)
    invoked = []
    monkeypatch.setattr(run_ke24, "_open_read_only_drive", lambda: ("service", {}, {}, b"secret"))
    monkeypatch.setattr(
        run_ke24, "execute_readonly_stages",
        lambda *args: invoked.append(args) or {"candidate_count": 83, "processed_rows": 2105},
    )
    assert run_ke24.run_preview(tmp_path, _env()) == 0
    assert len(invoked) == 1
    result = read_preview_report(tmp_path)
    assert result["status"] == "SUCCESS"
    assert result["metrics"] == {"processed_rows": 2105, "candidate_count": 83}
    assert not (tmp_path / "data/curated/.ke24_preview.lock").exists()


def test_preview_failure_is_sanitized_and_releases_lock(tmp_path, monkeypatch):
    _fake_project(tmp_path)
    monkeypatch.setattr(run_ke24, "_open_read_only_drive", lambda: ("service", {}, {}, b"secret"))
    def fail(*_args):
        raise RuntimeError("Sensitive Drive ID: SECRET-UNSAFE")
    monkeypatch.setattr(run_ke24, "execute_readonly_stages", fail)
    assert run_ke24.run_preview(tmp_path, _env()) == 1
    assert read_preview_report(tmp_path)["status"] == "FAILED"
    assert "SECRET-UNSAFE" not in report_file(tmp_path).read_text(encoding="utf-8")
    assert not (tmp_path / "data/curated/.ke24_preview.lock").exists()


def test_preview_blocks_when_new_raw_is_uncommitted(tmp_path, monkeypatch):
    _fake_project(tmp_path)
    monkeypatch.setattr(run_ke24, "_open_read_only_drive", lambda: ("service", {}, {}, b"secret"))
    monkeypatch.setattr(
        run_ke24, "execute_readonly_stages",
        lambda *args: (_ for _ in ()).throw(run_ke24.PreviewBlocked("new source")),
    )
    assert run_ke24.run_preview(tmp_path, _env()) == 1
    assert read_preview_report(tmp_path)["status"] == "BLOCKED"


def test_interrupted_preview_is_recorded_without_publication(tmp_path, monkeypatch):
    _fake_project(tmp_path)
    monkeypatch.setattr(run_ke24, "_open_read_only_drive", lambda: ("service", {}, {}, b"secret"))
    monkeypatch.setattr(
        run_ke24, "execute_readonly_stages",
        lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    assert run_ke24.run_preview(tmp_path, _env()) == 130
    assert read_preview_report(tmp_path)["status"] == "INTERRUPTED"
    assert not (tmp_path / "data/curated/.ke24_preview.lock").exists()


def test_existing_lock_prevents_drive_connection_and_preserves_report(tmp_path, monkeypatch):
    _fake_project(tmp_path)
    lock = tmp_path / "data/curated/.ke24_preview.lock"
    with exclusive_preview(tmp_path, "active"):
        monkeypatch.setattr(run_ke24, "_open_read_only_drive", lambda: pytest.fail("Drive must not connect"))
        assert run_ke24.run_preview(tmp_path, _env()) == 1
        assert lock.is_file()
        assert not report_file(tmp_path).exists()
    assert not lock.exists()


def test_status_mode_does_not_contact_drive(tmp_path, monkeypatch):
    monkeypatch.setattr(run_ke24, "_open_read_only_drive", lambda: pytest.fail("Do not authenticate"))
    assert run_ke24.run_last_status(tmp_path) == 1
    write_preview_report(
        tmp_path, run_id="offline123", started_at=datetime.now(timezone.utc),
        duration_seconds=1.0, status="BLOCKED",
    )
    assert run_ke24.run_last_status(tmp_path) == 0


def test_dry_run_does_not_create_local_status_file(tmp_path):
    _fake_project(tmp_path)
    assert run_ke24.run_dry_run(tmp_path, _env()) == 0
    assert not (tmp_path / "data/curated").exists()
