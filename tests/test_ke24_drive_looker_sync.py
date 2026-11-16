import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pandas as pd

from src.alerts.build_ke24_pretrained_outputs import build_pretrained_outputs
from src.loading import ke24_looker_sheet as looker

ROOT = Path(__file__).resolve().parents[1]


def token(char):
    return "TKN_" + char * 32


def policy():
    return json.loads((ROOT / "config" / "ke24_privacy_policy.json").read_text(encoding="utf-8"))


def make_history(numbers=(2,)):
    rows = []
    for n in numbers:
        rows.append({
            "_source_file": token("a"),
            "_source_file_sha256": token("b"),
            "_source_row_number": n,
            "company_code": token("c"),
            "reference_document": token("d"),
            "period_key": "2026-03",
            "profit_center": token("e"),
            "product": token("f"),
            "currency": "BRL", "currency_type": 10,
            "alert_level": "PENDING",
            "alert_reason": "classification_rules_not_approved",
            "classification_status": "PENDING_BUSINESS_RULE",
            "anomaly_score": 0.62,
            "anomaly_rank": n,
            "anomaly_flag": True,
            "training_p95_threshold": 0.55,
            "score_margin": 0.07,
            "reference_p95_flag": True,
            "model_version": "test_temporal_s42",
            "signal_method": "training_p95",
            "total_absolute_value": 500.0,
            "materiality_percentile": 100.0,
            "stability_rate": 80.0,
            "dominant_measure": "revenue",
            "dominant_measure_value": 500.0,
        })
    history, _ = build_pretrained_outputs(
        pd.DataFrame(rows), None, datetime(2026, 3, 10, tzinfo=timezone.utc), policy()
    )
    return history


class Request:
    def __init__(self, callback):
        self.callback = callback

    def execute(self):
        return self.callback()


class SheetApi:
    def __init__(self):
        self.rows = []
        self.write_calls = 0
        self.extra_tab = False
        self.grid = {"rowCount": 1000, "columnCount": len(looker.EXPORT_COLUMNS), "frozenRowCount": 1}

    def spreadsheets(self):
        return self

    def values(self):
        return SheetValues(self)

    def get(self, *, spreadsheetId, fields):
        sheets = [{"properties": {"sheetId": 0, "title": looker.SHEET_TITLE, "gridProperties": self.grid}}]
        if self.extra_tab:
            sheets.append({"properties": {"sheetId": 1, "title": "extra", "gridProperties": self.grid}})
        return Request(lambda: {"spreadsheetId": spreadsheetId, "sheets": sheets})

    def batchUpdate(self, *, spreadsheetId, body):
        def run():
            self.write_calls += 1
            req = body["requests"]
            self.grid = req[0]["updateSheetProperties"]["properties"]["gridProperties"]
            self.rows = []
            for row in req[1]["updateCells"]["rows"]:
                cells = []
                for c in row["values"]:
                    typed = c["userEnteredValue"]
                    cells.append(typed.get("stringValue", typed.get("numberValue")))
                self.rows.append(cells)
            return {}
        return Request(run)

    def create(self, *, body, fields):
        def run():
            assert len(body["sheets"]) == 1
            return {"spreadsheetId": "sheet_test_1", "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/example"}
        return Request(run)


class SheetValues:
    def __init__(self, parent):
        self.parent = parent

    def get(self, *, spreadsheetId, range, valueRenderOption):
        return Request(lambda: {"values": [list(row) for row in self.parent.rows]})


class DriveApi:
    def __init__(self, shared=False, can_edit=True):
        self.shared = shared
        self.can_edit = can_edit

    def files(self):
        return self

    def get(self, *, fileId, fields):
        return Request(lambda: {
            "id": fileId, "mimeType": looker.SHEET_MIME,
            "shared": self.shared, "trashed": False,
            "capabilities": {"canEdit": self.can_edit},
        })


def test_safe_export_contains_only_twelve_approved_columns():
    matrix = looker.build_matrix(make_history((2, 3)), policy())
    assert matrix[0] == looker.EXPORT_COLUMNS
    assert len(matrix) == 3
    assert "reference_document" not in matrix[0]
    assert "total_absolute_value" not in matrix[0]
    assert "dominant_measure_value" not in matrix[0]
    assert "_source_file" not in matrix[0]
    assert matrix[1][-1] == 1


def test_formula_like_measure_is_written_as_literal_string():
    history = make_history()
    history.loc[0, "dominant_measure"] = '=HYPERLINK("https://example.org","x")'
    matrix = looker.build_matrix(history, policy())
    req = looker.build_atomic_update(matrix, {"sheetId": 0, "gridProperties": {"rowCount": 1000}})
    cells = req["requests"][1]["updateCells"]["rows"]
    assert cells[0]["values"][7]["userEnteredValue"]["stringValue"] == "stability_rate"
    assert cells[1]["values"][9]["userEnteredValue"]["stringValue"].startswith("=HYPERLINK")
    assert "formulaValue" not in str(req)


def test_publish_and_repeat_without_duplicate_write():
    sheet, drive = SheetApi(), DriveApi()
    matrix = looker.build_matrix(make_history(), policy())
    result = looker.publish_sheet(sheet, drive, "sheet_test_1", matrix, [])
    assert result == {"published": True, "rows": 1}
    result_again = looker.publish_sheet(sheet, drive, "sheet_test_1", matrix, matrix)
    assert result_again == {"published": False, "rows": 1}
    assert sheet.write_calls == 1


def test_new_alert_adds_row_without_duplicate():
    sheet, drive = SheetApi(), DriveApi()
    old = looker.build_matrix(make_history((2,)), policy())
    new = looker.build_matrix(make_history((2, 3)), policy())
    looker.publish_sheet(sheet, drive, "sheet_test_1", old)
    result = looker.publish_sheet(sheet, drive, "sheet_test_1", new, old)
    assert result["published"] is True
    assert len(sheet.rows) == 3
    assert len({r[0] for r in sheet.rows[1:]}) == 2


def test_manual_change_blocks_overwrite():
    sheet, drive = SheetApi(), DriveApi()
    desired = looker.build_matrix(make_history(), policy())
    sheet.rows = [list(row) for row in desired]
    sheet.rows[1][5] = "HIGH_CLASSIFIED_WITHOUT_APPROVAL"
    with pytest.raises(RuntimeError, match="alterado"):
        looker.publish_sheet(sheet, drive, "sheet_test_1", desired)
    assert sheet.write_calls == 0


def test_wrong_header_blocks():
    desired = looker.build_matrix(make_history(), policy())
    wrong = [list(desired[0])]
    wrong[0][1] = "company_real"
    with pytest.raises(RuntimeError, match="Cabecalho"):
        looker.check_existing_sheet(wrong, desired)


def test_unknown_alert_blocks():
    desired = looker.build_matrix(make_history(), policy())
    unknown = [desired[0], list(desired[1])]
    unknown[1][0] = "ALT_" + "f" * 32
    with pytest.raises(RuntimeError, match="inexistente"):
        looker.check_existing_sheet(unknown, desired)


def test_shared_target_blocks():
    sheet, drive = SheetApi(), DriveApi(shared=True)
    with pytest.raises(RuntimeError, match="compartilhada"):
        looker.publish_sheet(sheet, drive, "sheet_test_1", looker.build_matrix(make_history(), policy()))


def test_extra_tab_blocks():
    sheet, drive = SheetApi(), DriveApi()
    sheet.extra_tab = True
    with pytest.raises(RuntimeError, match="dedicada"):
        looker.inspect_sheet(sheet, drive, "sheet_test_1")


def test_unprotected_identifier_blocks_before_sheets():
    history = make_history()
    history.loc[0, "company_code"] = "REAL_COMPANY"
    with pytest.raises(ValueError):
        looker.build_matrix(history, policy())


def test_create_sheet_does_not_write_data():
    sheet = SheetApi()
    result = looker.create_empty_sheet(sheet)
    assert result["spreadsheetId"] == "sheet_test_1"
    assert sheet.write_calls == 0


def test_previous_values_changed_blocks_write():
    sheet, drive = SheetApi(), DriveApi()
    new = looker.build_matrix(make_history(), policy())
    with pytest.raises(RuntimeError, match="mudou"):
        looker.publish_sheet(sheet, drive, "sheet_test_1", new, previous_values=["different"])
    assert sheet.write_calls == 0
