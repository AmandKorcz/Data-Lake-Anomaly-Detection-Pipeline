from copy import deepcopy

import pandas as pd
import pytest

from src.review import ke24_review_sheet as sheet
from src.review import ke24_review_trusted_snapshot as snap
from src.review import run_ke24_review_sheet as cli
from test_ke24_review_google_sheets import FakeDrive, FakeSheets, alerts, A, B

KEY = b'x' * 32
SID = 'sheet_example_012345'


def setup(tmp_path, ids=()):
    api, drive = FakeSheets(), FakeDrive()
    api.matrix = sheet.build_review_sheet_matrix(alerts(ids), [])
    baseline = tmp_path / 'baseline.json'
    snap.save_snapshot(KEY, SID, 7, api.matrix, path=baseline)
    return api, drive, baseline


def sync(api, drive, path, ids=(A,), *, publish=False):
    return cli.reconcile_queue(api, drive, SID, alerts(ids),
                               key=KEY, publish=publish, snapshot_file=path)


def test_preview_is_read_only(tmp_path):
    api, drive, path = setup(tmp_path)
    result = sync(api, drive, path, (A, B))
    assert result['new_alerts'] == 2
    assert result['published'] is False
    assert api.appends == 0 and len(api.matrix) == 1


def test_publish_appends_only_new_rows(tmp_path):
    api, drive, path = setup(tmp_path, (A,))
    result = sync(api, drive, path, (A, B), publish=True)
    assert result['published'] is True
    assert result['new_alerts'] == 1
    assert api.appends == 1
    assert len(api.matrix) == 3
    assert snap.load_snapshot(KEY, SID, 7, path=path) == api.matrix


def test_second_publish_is_idempotent(tmp_path):
    api, drive, path = setup(tmp_path)
    assert sync(api, drive, path, (A, B), publish=True)['published']
    assert sync(api, drive, path, (A, B), publish=True)['published'] is False
    assert api.appends == 1


def test_existing_request_kept_when_appending(tmp_path):
    api, drive, path = setup(tmp_path, (A,))
    api.matrix[1][-1] = 'IN_REVIEW'
    summary = sync(api, drive, path, (A, B), publish=True)
    assert summary['requests_not_authenticated'] == 1
    assert api.matrix[1][4] == 'PENDING'
    assert api.matrix[1][-1] == 'IN_REVIEW'
    assert api.matrix[2][-1] == 'PENDING'
    assert snap.load_snapshot(KEY, SID, 7, path=path)[1][-1] == 'IN_REVIEW'


def test_manual_technical_change_blocks_without_api_write(tmp_path):
    api, drive, path = setup(tmp_path, (A,))
    api.matrix[1][1] = '2026-10'
    with pytest.raises(ValueError):
        sync(api, drive, path, (A, B), publish=True)
    assert api.appends == 0


def test_unknown_alert_blocks_without_api_write(tmp_path):
    api, drive, path = setup(tmp_path, (A,))
    api.matrix.append(sheet.build_review_sheet_matrix(alerts((B,)), [])[1])
    with pytest.raises(ValueError):
        sync(api, drive, path, (A, B), publish=True)
    assert api.appends == 0


def test_invalid_status_transition_blocks(tmp_path):
    api, drive, path = setup(tmp_path, (A,))
    api.matrix[1][-1] = 'CORRECTED'
    with pytest.raises(ValueError):
        sync(api, drive, path, (A, B), publish=True)
    assert api.appends == 0


def test_missing_trusted_baseline_never_creates_one(tmp_path):
    api, drive, path = setup(tmp_path)
    path.unlink()
    with pytest.raises(RuntimeError, match='Snapshot'):
        sync(api, drive, path, (A, B), publish=True)
    assert api.appends == 0


def test_shared_sheet_blocked(tmp_path):
    api, drive, path = setup(tmp_path)
    drive.target['shared'] = True
    with pytest.raises(RuntimeError, match='compartilhada'):
        sync(api, drive, path, (A,), publish=True)
    assert api.appends == 0


def test_race_between_preflight_and_append_blocks(tmp_path):
    api, drive, path = setup(tmp_path, (A,))
    def alter_on_second_read(service):
        if service.reads == 2:
            service.matrix[1][-1] = 'IN_REVIEW'
    api.on_read = alter_on_second_read
    with pytest.raises(cli.QueueReconciliationRequired, match='alterada'):
        sync(api, drive, path, (A, B), publish=True)
    assert api.appends == 0


def test_unverified_after_write_fails_closed(tmp_path):
    api, drive, path = setup(tmp_path, (A,))
    baseline = deepcopy(api.matrix)
    def mutate_after_append(service):
        service.matrix[1][1] = '2026-10'
    api.on_append = mutate_after_append
    with pytest.raises(cli.QueueReconciliationRequired, match='indeterminada'):
        sync(api, drive, path, (A, B), publish=True)
    assert api.appends == 1
    assert snap.load_snapshot(KEY, SID, 7, path=path) == baseline


def test_permitted_reorder_not_rewritten(tmp_path):
    api, drive, path = setup(tmp_path, (A, B))
    api.matrix = [api.matrix[0], api.matrix[2], api.matrix[1]]
    summary = sync(api, drive, path, (A, B), publish=True)
    assert summary['new_alerts'] == 0
    assert api.appends == 0


def test_no_identifiers_in_summary(tmp_path):
    api, drive, path = setup(tmp_path, (A,))
    result = sync(api, drive, path, (A, B))
    assert 'ALT_' not in str(result)
    assert all(type(v) in (int, bool) for v in result.values())


def test_cli_rejects_publish_without_confirmation():
    with pytest.raises(SystemExit) as error:
        cli.main(['--publish'])
    assert error.value.code == 2


def test_cli_help_offline():
    with pytest.raises(SystemExit) as error:
        cli.main(['--help'])
    assert error.value.code == 0
