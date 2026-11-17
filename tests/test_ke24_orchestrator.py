from pathlib import Path

from src.pipeline import run_ke24


def _fake_files(tmp_path):
    for _, file_path in run_ke24.STEPS:
        path = tmp_path / file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# fixture\n', encoding='utf-8')


def test_components_and_existing_environment(tmp_path):
    _fake_files(tmp_path)
    result = run_ke24.inspect_cloud_flow(
        tmp_path, {"KE24_DRIVE_RAW_FOLDER_ID": "pasta-pre-configurada"}
    )
    assert result["ready_for_review"] is True
    assert len(result["steps"]) == 5
    assert run_ke24.run_dry_run(
        tmp_path, {"KE24_DRIVE_RAW_FOLDER_ID": "pasta-pre-configurada"}
    ) == 0


def test_missing_source_variable_fails_closed(tmp_path):
    _fake_files(tmp_path)
    assert run_ke24.inspect_cloud_flow(tmp_path, {})["ready_for_review"] is False
    assert run_ke24.run_dry_run(tmp_path, {}) == 1


def test_missing_entrypoint_fails_closed(tmp_path):
    _fake_files(tmp_path)
    (tmp_path / run_ke24.STEPS[-1][1]).unlink()
    result = run_ke24.inspect_cloud_flow(
        tmp_path, {"KE24_DRIVE_RAW_FOLDER_ID": "already-configured"}
    )
    assert result["ready_for_review"] is False
    assert run_ke24.run_dry_run(
        tmp_path, {"KE24_DRIVE_RAW_FOLDER_ID": "already-configured"}
    ) == 1


def test_local_raw_ignored(tmp_path):
    _fake_files(tmp_path)
    local_raw = tmp_path / 'data' / 'raw'
    local_raw.mkdir(parents=True)
    (local_raw / 'unrelated.xlsx').write_bytes(b'not a spreadsheet')
    assert run_ke24.inspect_cloud_flow(
        tmp_path, {"KE24_DRIVE_RAW_FOLDER_ID": "already-configured"}
    )["ready_for_review"] is True
