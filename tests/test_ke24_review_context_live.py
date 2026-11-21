from copy import deepcopy

import pytest

from src.review import ke24_review_google_sheets as api
from src.review import ke24_review_trusted_snapshot as snap
from src.review import run_ke24_review_sheet as queue_cli
from src.review.ke24_review_context import CONTEXT_COLUMNS, CONTEXT_HEADERS, CONTEXT_SHEET_TITLE
from test_ke24_review_context_sync import A, B, ALIASES, queue, records
from test_ke24_review_google_sheets import FakeDrive, FakeSheets, Request
from tools import ke24_review_context_tab as context_cli

KEY = b'x'*32
SID = 'sheet_example_012345'


class ContextFake(FakeSheets):
    def __init__(self, ids=(A,B)):
        super().__init__()
        self.matrix = queue(ids)
        self.context_matrix = None
        self.context_appends = 0
        self.context_created = 0
        self.on_queue_read = None
        self.queue_reads = 0

    def get(self, *, spreadsheetId, **kwargs):
        assert spreadsheetId == SID
        if 'range' in kwargs:
            if CONTEXT_SHEET_TITLE in kwargs['range']:
                assert self.context_matrix is not None
                return Request(lambda: {'values': deepcopy(self.context_matrix)})
            def read_queue():
                self.queue_reads += 1
                if self.on_queue_read:
                    self.on_queue_read(self)
                return {'values': deepcopy(self.matrix)}
            return Request(read_queue)
        return Request(lambda: deepcopy(self.metadata))

    def batchUpdate(self, *, spreadsheetId, body):
        assert spreadsheetId == SID
        def run():
            self.batch_bodies.append(deepcopy(body))
            for request in body['requests']:
                if 'addSheet' in request:
                    self.context_created += 1
                    props = deepcopy(request['addSheet']['properties'])
                    self.metadata['sheets'].append({
                        'properties': props,
                        'protectedRanges': [],
                    })
                if 'updateCells' in request:
                    self.context_matrix = [list(CONTEXT_HEADERS)]
                if 'addProtectedRange' in request:
                    protection = deepcopy(request['addProtectedRange']['protectedRange'])
                    protection['protectedRangeId'] = 91
                    self.metadata['sheets'][-1]['protectedRanges'].append(protection)
                if 'appendCells' in request:
                    assert self.context_matrix is not None
                    self.context_appends += 1
                    for row in request['appendCells']['rows']:
                        self.context_matrix.append([
                            cell['userEnteredValue']['stringValue']
                            for cell in row['values']
                        ])
            return {}
        return Request(run)


def prepared(tmp_path, ids=(A,B)):
    sheets, drive = ContextFake(ids), FakeDrive()
    path = tmp_path/'signed.json'
    snap.save_snapshot(KEY,SID,7,sheets.matrix,path=path)
    return sheets, drive, path


def action(sheets,drive,path,ids=(A,B),publish=False):
    return context_cli.reconcile_context(
        sheets,drive,SID,records(ids),ALIASES,key=KEY,
        publish=publish,snapshot_file=path,
    )


def test_preview_readonly_and_never_creates_tab(tmp_path):
    sheets,drive,path=prepared(tmp_path)
    summary=action(sheets,drive,path)
    assert summary == {'queue_alerts': 2, 'context_existing': 0,
                       'context_new': 2, 'requests_not_authenticated': 0,
                       'published': False}
    assert sheets.context_created == sheets.context_appends == 0
    assert len(sheets.metadata['sheets']) == 1


def test_first_publish_creates_protected_readonly_tab_only(tmp_path):
    sheets,drive,path=prepared(tmp_path)
    original=deepcopy(sheets.matrix)
    summary=action(sheets,drive,path,publish=True)
    assert summary['published'] and summary['context_new']==2
    assert sheets.context_created==1 and sheets.context_appends==1
    assert sheets.matrix==original
    assert snap.load_snapshot(KEY,SID,7,path=path)==original
    assert len(sheets.metadata['sheets'])==2
    assert sheets.context_matrix[0]==list(CONTEXT_HEADERS)
    assert len(sheets.context_matrix)==3
    assert sheets.context_matrix[1][1]=='Empresa Alfa'
    assert api.verify_private_review_sheet(sheets,drive,SID)==7
    assert api.get_context_sheet_id(sheets,drive,SID)==1_000_001


def test_existing_context_is_idempotent(tmp_path):
    sheets,drive,path=prepared(tmp_path)
    action(sheets,drive,path,publish=True)
    result=action(sheets,drive,path,publish=True)
    assert result['context_existing']==2 and result['context_new']==0
    assert not result['published']
    assert sheets.context_created==1 and sheets.context_appends==1


def test_queue_preview_still_operates_with_second_tab(tmp_path):
    sheets,drive,path=prepared(tmp_path)
    action(sheets,drive,path,publish=True)
    result=queue_cli.reconcile_queue(sheets,drive,SID,records(),key=KEY,
                                     publish=False,snapshot_file=path)
    assert result['existing_alerts']==2 and result['new_alerts']==0


def test_context_preserves_unverified_status_request(tmp_path):
    sheets,drive,path=prepared(tmp_path)
    sheets.matrix[1][-1]='IN_REVIEW'
    action(sheets,drive,path,publish=True)
    assert sheets.matrix[1][-1]=='IN_REVIEW'
    assert sheets.matrix[1][4]=='PENDING'
    assert action(sheets,drive,path)['requests_not_authenticated']==1


def test_invalid_extra_tab_blocks_queue_and_context(tmp_path):
    sheets,drive,path=prepared(tmp_path)
    sheets.metadata['sheets'].append({'properties': {
        'sheetId': 1_000_001,'title':'Untrusted', 'gridProperties':{}},
        'protectedRanges':[]})
    with pytest.raises(RuntimeError,match='nao autorizada'):
        action(sheets,drive,path,publish=True)
    assert sheets.context_created==0


def test_context_protection_tampering_blocks(tmp_path):
    sheets,drive,path=prepared(tmp_path)
    action(sheets,drive,path,publish=True)
    sheets.metadata['sheets'][1]['protectedRanges'][0]['editors']['users'].append('other@example.com')
    with pytest.raises(RuntimeError, match='Permissao'):
        action(sheets,drive,path)


def test_missing_context_protection_blocks(tmp_path):
    sheets,drive,path=prepared(tmp_path)
    action(sheets,drive,path,publish=True)
    sheets.metadata['sheets'][1]['protectedRanges']=[]
    with pytest.raises(RuntimeError, match='protecao'):
        action(sheets,drive,path)


def test_private_tab_content_tampered_blocks(tmp_path):
    sheets,drive,path=prepared(tmp_path)
    action(sheets,drive,path,publish=True)
    sheets.context_matrix[1][1]='Empresa Alterada'
    with pytest.raises(RuntimeError,match='sobrescrever'):
        action(sheets,drive,path,publish=True)
    assert sheets.context_appends==1


def test_shared_workbook_blocks_context_tab(tmp_path):
    sheets,drive,path=prepared(tmp_path)
    drive.target['shared']=True
    with pytest.raises(RuntimeError,match='compartilhada'):
        action(sheets,drive,path,publish=True)
    assert sheets.context_created==0


def test_queue_edit_race_detected_before_creation(tmp_path):
    sheets,drive,path=prepared(tmp_path)
    def race(s):
        if s.queue_reads==2:
            s.matrix[1][-1]='IN_REVIEW'
    sheets.on_queue_read=race
    with pytest.raises(RuntimeError,match='alterada'):
        action(sheets,drive,path,publish=True)
    assert sheets.context_created==0


def test_queue_lacks_new_history_alerts_blocks(tmp_path):
    sheets,drive,path=prepared(tmp_path,(A,))
    with pytest.raises(RuntimeError,match='Novos alertas'):
        action(sheets,drive,path,ids=(A,B),publish=True)
    assert sheets.context_created==0


def test_later_context_append_is_incremental(tmp_path):
    sheets,drive,path=prepared(tmp_path,(A,))
    action(sheets,drive,path,ids=(A,),publish=True)
    sheets.matrix=queue((A,B))
    snap.save_snapshot(KEY,SID,7,sheets.matrix,path=path)
    result=action(sheets,drive,path,ids=(A,B),publish=True)
    assert result['context_existing']==1 and result['context_new']==1
    assert sheets.context_created==1 and sheets.context_appends==2
    assert len(sheets.context_matrix)==3


def test_cli_publish_requires_explicit_confirmation():
    with pytest.raises(SystemExit) as error:
        context_cli.main(['--publish'])
    assert error.value.code==2


def test_cli_help_needs_no_network():
    with pytest.raises(SystemExit) as error:
        context_cli.main(['--help'])
    assert error.value.code==0


def test_unprotected_holes_in_context_are_blocked(tmp_path):
    sheets,drive,path=prepared(tmp_path)
    action(sheets,drive,path,publish=True)
    sheets.metadata['sheets'][1]['protectedRanges'][0]['unprotectedRanges']=[
        {'sheetId':1_000_001, 'startRowIndex':1, 'endRowIndex':2}]
    with pytest.raises(RuntimeError,match='Protecao'):
        action(sheets,drive,path)


def test_private_queue_protection_with_holes_is_rejected(tmp_path):
    sheets,drive,path=prepared(tmp_path)
    sheets.metadata['sheets'][0]['protectedRanges'][0]['unprotectedRanges']=[
        {'sheetId':7, 'startRowIndex':1, 'endRowIndex':2}]
    with pytest.raises(RuntimeError,match='Protecao'):
        action(sheets,drive,path)


def test_context_header_tampered_blocks_before_write(tmp_path):
    sheets,drive,path=prepared(tmp_path)
    action(sheets,drive,path,publish=True)
    sheets.context_matrix[0][0]='OTHER'
    with pytest.raises(ValueError,match='Cabecalho'):
        action(sheets,drive,path,publish=True)
    assert sheets.context_appends==1
