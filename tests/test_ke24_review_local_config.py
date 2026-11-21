import os
from pathlib import Path

import pytest

from src.config import ke24_local_settings as settings
from src.review import ke24_review_google_sheets as sheets


def _set_folders(monkeypatch):
    for suffix, value in [('RAW','raw_example'),('PROCESSED','processed_example'),('ALERTS','alerts_example')]:
        monkeypatch.setenv(f'KE24_DRIVE_{suffix}_FOLDER_ID',value)


def test_local_review_id_is_loaded(tmp_path, monkeypatch):
    _set_folders(monkeypatch)
    file = tmp_path / '.env'
    file.write_text('KE24_REVIEW_SPREADSHEET_ID=review_example_1234\n', encoding='utf-8')
    assert settings.load_ke24_local_settings(file) == ('KE24_REVIEW_SPREADSHEET_ID',)
    assert sheets.get_spreadsheet_id(dict(os.environ)) == 'review_example_1234'


def test_local_review_id_takes_precedence_over_windows(tmp_path, monkeypatch):
    _set_folders(monkeypatch)
    monkeypatch.setenv('KE24_REVIEW_SPREADSHEET_ID', 'old_example_99999')
    file = tmp_path / '.env'
    file.write_text('KE24_REVIEW_SPREADSHEET_ID=review_example_1234\n',encoding='utf-8')
    settings.load_ke24_local_settings(file)
    assert os.environ['KE24_REVIEW_SPREADSHEET_ID'] == 'review_example_1234'


def test_duplicate_technical_and_review_sheets_block(tmp_path, monkeypatch):
    _set_folders(monkeypatch)
    file = tmp_path / '.env'
    file.write_text('KE24_REVIEW_SPREADSHEET_ID=same_example_12345\nKE24_LOOKER_SPREADSHEET_ID=same_example_12345\n', encoding='utf-8')
    with pytest.raises(ValueError, match='diferentes'):
        settings.load_ke24_local_settings(file)


def test_invalid_review_id_no_partial_config(tmp_path, monkeypatch):
    _set_folders(monkeypatch)
    file = tmp_path / '.env'
    file.write_text('KE24_DRIVE_RAW_FOLDER_ID=new_raw_example\nKE24_REVIEW_SPREADSHEET_ID=bad/slug\n', encoding='utf-8')
    with pytest.raises(ValueError):
        settings.load_ke24_local_settings(file)
    assert os.environ['KE24_DRIVE_RAW_FOLDER_ID'] == 'raw_example'


def test_save_from_environment_includes_review_id(tmp_path, monkeypatch):
    _set_folders(monkeypatch)
    monkeypatch.setenv('KE24_REVIEW_SPREADSHEET_ID','review_example_1234')
    monkeypatch.setenv('KE24_HMAC_KEY','dont_include_secret')
    path = tmp_path / 'config' / '.env'
    assert 'KE24_REVIEW_SPREADSHEET_ID' in settings.save_from_environment(path)
    text = path.read_text(encoding='utf-8')
    assert 'KE24_REVIEW_SPREADSHEET_ID=review_example_1234' in text
    assert 'dont_include_secret' not in text
    with pytest.raises(FileExistsError):
        settings.save_from_environment(path)


def test_live_getter_loads_settings_before_oauth(monkeypatch):
    _set_folders(monkeypatch)
    monkeypatch.delenv('KE24_REVIEW_SPREADSHEET_ID',raising=False)
    def load():
        os.environ['KE24_REVIEW_SPREADSHEET_ID'] = 'review_example_1234'
        return ('KE24_REVIEW_SPREADSHEET_ID',)
    monkeypatch.setattr(settings, 'load_ke24_local_settings', load)
    assert sheets.get_spreadsheet_id() == 'review_example_1234'


def test_explicit_env_never_looks_up_file(monkeypatch):
    def no_file():
        pytest.fail('Explicit environ should not read config/.env')
    monkeypatch.setattr(settings,'load_ke24_local_settings',no_file)
    assert sheets.get_spreadsheet_id({'KE24_REVIEW_SPREADSHEET_ID':'review_example_1234'}) == 'review_example_1234'
