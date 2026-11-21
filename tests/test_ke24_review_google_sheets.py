from copy import deepcopy

import pandas as pd
import pytest

from src.review import ke24_review_google_sheets as live
from src.review import ke24_review_sheet as sheet

A = 'ALT_' + 'a' * 32
B = 'ALT_' + 'b' * 32


def alerts(ids=(A,)):
    return pd.DataFrame({
        'alert_id': list(ids),
        'period_key': ['2026-11'] * len(ids),
        'alert_level': ['PENDING'] * len(ids),
        'classification_status': ['PENDING_BUSINESS_RULE'] * len(ids),
    })


class Request:
    def __init__(self, action):
        self.action = action

    def execute(self):
        return self.action()


class FakeSheets:
    def __init__(self):
        self.matrix = sheet.build_review_sheet_matrix(alerts(()), [])
        self.metadata = {
            'spreadsheetId': 'sheet_example_012345',
            'sheets': [{
                'properties': {'sheetId': 7, 'title': sheet.SHEET_TITLE,
                               'gridProperties': {'rowCount': sheet.MAX_ALERTS + 1,
                                                  'columnCount': 8, 'frozenRowCount': 1}},
                'protectedRanges': [{
                    'protectedRangeId': 28,
                    'range': {'sheetId': 7, 'startColumnIndex': 0, 'endColumnIndex': 7},
                    'warningOnly': False,
                    'editors': {'users': ['owner@example.com'],
                                'groups': [], 'domainUsersCanEdit': False},
                }],
            }],
        }
        self.appends = 0
        self.batch_bodies = []
        self.on_read = None
        self.reads = 0
        self.on_append = None

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, *, spreadsheetId, **kwargs):
        assert spreadsheetId == 'sheet_example_012345'
        if 'range' in kwargs:
            def get_values():
                self.reads += 1
                if self.on_read is not None:
                    self.on_read(self)
                return {'values': deepcopy(self.matrix)}
            return Request(get_values)
        return Request(lambda: deepcopy(self.metadata))

    def batchUpdate(self, *, spreadsheetId, body):
        def run():
            self.batch_bodies.append(deepcopy(body))
            for req in body['requests']:
                if 'appendCells' in req:
                    self.appends += 1
                    for row in req['appendCells']['rows']:
                        out = []
                        for cell in row['values']:
                            raw = cell['userEnteredValue']
                            out.append(raw.get('numberValue', raw.get('stringValue')))
                        self.matrix.append(out)
                    if self.on_append:
                        self.on_append(self)
            return {}
        return Request(run)

    def create(self, *, body, fields):
        assert body['sheets'][0]['properties']['title'] == sheet.SHEET_TITLE
        return Request(lambda: {
            'spreadsheetId': 'sheet_example_012345',
            'spreadsheetUrl': 'https://docs.google.com/spreadsheets/d/sheet_example_012345',
            'sheets': [{'properties': {'sheetId': 7, 'title': sheet.SHEET_TITLE}}],
        })


class FakeDrive:
    def __init__(self):
        self.target = {
            'id': 'sheet_example_012345', 'mimeType': live.SHEET_MIME,
            'shared': False, 'trashed': False,
            'capabilities': {'canEdit': True},
            'owners': [{'emailAddress': 'owner@example.com'}],
        }

    def files(self):
        return self

    def get(self, *, fileId, fields):
        assert fileId == self.target['id']
        return Request(lambda: deepcopy(self.target))


def test_environment_separate_from_technical_sheet():
    env = {live.SPREADSHEET_ENV: 'sheet_example_012345',
           'KE24_LOOKER_SPREADSHEET_ID': 'sheet_different_1234'}
    assert live.get_spreadsheet_id(env) == 'sheet_example_012345'
    env['KE24_LOOKER_SPREADSHEET_ID'] = env[live.SPREADSHEET_ENV]
    with pytest.raises(ValueError, match='diferente'):
        live.get_spreadsheet_id(env)


@pytest.mark.parametrize('value', ['', 'x', '../unsafe', 'https://example.com', None])
def test_spreadsheet_id_validation(value):
    with pytest.raises(ValueError):
        live.get_spreadsheet_id({live.SPREADSHEET_ENV: value})


def test_private_sheet_accepts_exact_protection():
    assert live.verify_private_review_sheet(FakeSheets(), FakeDrive(), 'sheet_example_012345') == 7


def test_shared_file_fails_closed():
    drive = FakeDrive()
    drive.target['shared'] = True
    with pytest.raises(RuntimeError, match='compartilhada'):
        live.verify_private_review_sheet(FakeSheets(), drive, 'sheet_example_012345')


@pytest.mark.parametrize('tamper', ['warning', 'removed', 'extra-editor', 'domain', 'wrong-range', 'wrong-tab'])
def test_protection_tampering_fails_closed(tamper):
    api = FakeSheets()
    entry = api.metadata['sheets'][0]
    protect = entry['protectedRanges'][0]
    if tamper == 'warning':
        protect['warningOnly'] = True
    elif tamper == 'removed':
        entry['protectedRanges'] = []
    elif tamper == 'extra-editor':
        protect['editors']['users'].append('other@example.com')
    elif tamper == 'domain':
        protect['editors']['domainUsersCanEdit'] = True
    elif tamper == 'wrong-range':
        protect['range']['endColumnIndex'] = 8
    else:
        entry['properties']['title'] = 'other'
    with pytest.raises(RuntimeError):
        live.verify_private_review_sheet(api, FakeDrive(), 'sheet_example_012345')


def test_initialization_requests_protect_a_to_g_only():
    req = live.build_initialization_requests(7, 'owner@example.com')
    assert len(req) == 3
    region = req[-1]['addProtectedRange']['protectedRange']
    assert region['range']['endColumnIndex'] == 7
    assert region['warningOnly'] is False
    assert region['editors']['users'] == ['owner@example.com']
    assert req[1]['setDataValidation']['range']['startColumnIndex'] == 7
    assert 'formulaValue' not in str(req)


def test_read_preserves_header_and_rejects_fake_formula():
    api = FakeSheets()
    api.matrix = sheet.build_review_sheet_matrix(alerts(), [])
    assert live.read_review_sheet_matrix(api, 'sheet_example_012345') == api.matrix
    api.matrix[1][-1] = '=IMPORTXML("http://bad")'
    with pytest.raises(ValueError):
        live.read_review_sheet_matrix(api, 'sheet_example_012345')


def test_append_is_only_appendcells_and_string_literals():
    api = FakeSheets()
    rows = sheet.build_review_sheet_matrix(alerts((A, B)), [])[1:]
    live.append_new_review_alerts(api, 'sheet_example_012345', 7, rows)
    assert api.appends == 1
    assert len(api.matrix) == 3
    req = api.batch_bodies[-1]['requests']
    assert len(req) == 1 and 'appendCells' in req[0]
    assert 'updateCells' not in str(req)
    assert 'formulaValue' not in str(req)
    assert api.matrix[1][-1] == api.matrix[2][-1] == 'PENDING'


def test_create_request_starts_private_without_data():
    api = FakeSheets()
    created = live.create_private_review_sheet(api, FakeDrive())
    assert created['spreadsheetId'] == 'sheet_example_012345'
    assert api.appends == 0


def test_initialization_adds_no_alerts():
    api, drive = FakeSheets(), FakeDrive()
    created = live.create_private_review_sheet(api, drive)
    assert live.initialize_private_review_sheet(api, drive, created) == 7
    assert api.appends == 0
    assert live.read_review_sheet_matrix(api, 'sheet_example_012345') == [list(sheet.SHEET_COLUMNS)]
