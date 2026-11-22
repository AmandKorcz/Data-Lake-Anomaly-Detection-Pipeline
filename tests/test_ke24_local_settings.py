import pytest

from src.config.ke24_local_settings import (
    load_ke24_local_settings,
    save_from_environment,
)


def _set_folders(monkeypatch):
    monkeypatch.setenv("KE24_DRIVE_RAW_FOLDER_ID", "raw_from_windows")
    monkeypatch.setenv("KE24_DRIVE_PROCESSED_FOLDER_ID", "processed_from_windows")
    monkeypatch.setenv("KE24_DRIVE_ALERTS_FOLDER_ID", "alerts_from_windows")


def test_config_missing_preserves_existing_env(tmp_path, monkeypatch):
    _set_folders(monkeypatch)
    assert load_ke24_local_settings(tmp_path / ".env") == ()
    assert __import__("os").environ["KE24_DRIVE_RAW_FOLDER_ID"] == "raw_from_windows"


def test_local_file_takes_priority_for_all_folder_ids(tmp_path, monkeypatch):
    _set_folders(monkeypatch)
    file = tmp_path / ".env"
    file.write_text(
        "KE24_DRIVE_RAW_FOLDER_ID=raw_from_file\n"
        "KE24_DRIVE_PROCESSED_FOLDER_ID=processed_from_file\n"
        "KE24_DRIVE_ALERTS_FOLDER_ID=alerts_from_file\n",
        encoding="utf-8",
    )
    keys = load_ke24_local_settings(file)
    import os
    assert len(keys) == 3
    assert os.environ["KE24_DRIVE_RAW_FOLDER_ID"] == "raw_from_file"
    assert os.environ["KE24_DRIVE_PROCESSED_FOLDER_ID"] == "processed_from_file"
    assert os.environ["KE24_DRIVE_ALERTS_FOLDER_ID"] == "alerts_from_file"


def test_invalid_local_config_does_not_mutate_environment(tmp_path, monkeypatch):
    _set_folders(monkeypatch)
    file = tmp_path / ".env"
    file.write_text(
        "KE24_DRIVE_RAW_FOLDER_ID=good_id\n"
        "KE24_DRIVE_PROCESSED_FOLDER_ID=id with spaces\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="KE24_DRIVE_PROCESSED_FOLDER_ID"):
        load_ke24_local_settings(file)
    import os
    assert os.environ["KE24_DRIVE_RAW_FOLDER_ID"] == "raw_from_windows"


def test_duplicate_folder_ids_blocked(tmp_path, monkeypatch):
    _set_folders(monkeypatch)
    file = tmp_path / ".env"
    file.write_text(
        "KE24_DRIVE_RAW_FOLDER_ID=same_folder\n"
        "KE24_DRIVE_PROCESSED_FOLDER_ID=same_folder\n"
        "KE24_DRIVE_ALERTS_FOLDER_ID=third_folder\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="diferentes"):
        load_ke24_local_settings(file)


def test_save_copies_only_allowed_identifiers_and_never_overwrites(tmp_path, monkeypatch):
    _set_folders(monkeypatch)
    monkeypatch.delenv("KE24_REVIEW_SPREADSHEET_ID",raising=False)
    monkeypatch.setenv("KE24_LOOKER_SPREADSHEET_ID", "sheet_existing")
    monkeypatch.setenv("KE24_HMAC_KEY", "not-for-config")
    monkeypatch.setenv("KE24_OAUTH_TOKEN_FILE", "never-save-this")
    file = tmp_path / "config" / ".env"
    keys = save_from_environment(file)
    assert len(keys) == 4
    saved = file.read_text(encoding="utf-8")
    assert "raw_from_windows" in saved
    assert "sheet_existing" in saved
    assert "not-for-config" not in saved
    assert "never-save-this" not in saved
    with pytest.raises(FileExistsError):
        save_from_environment(file)
    assert file.read_text(encoding="utf-8") == saved


def test_save_requires_three_folder_ids(tmp_path, monkeypatch):
    _set_folders(monkeypatch)
    monkeypatch.delenv("KE24_DRIVE_ALERTS_FOLDER_ID")
    file = tmp_path / "config" / ".env"
    with pytest.raises(ValueError, match="KE24_DRIVE_ALERTS_FOLDER_ID"):
        save_from_environment(file)
    assert not file.exists()
