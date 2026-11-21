from copy import deepcopy

import pandas as pd
import pytest

from src.review import ke24_review_sheet as sheet
from src.review import ke24_review_workflow as workflow

A = "ALT_" + "a" * 32
B = "ALT_" + "b" * 32
C = "ALT_" + "c" * 32


def alerts(ids=(A, B)):
    return pd.DataFrame({
        "alert_id": list(ids), "period_key": ["2026-10"] * len(ids),
        "alert_level": ["PENDING"] * len(ids),
        "classification_status": ["PENDING_BUSINESS_RULE"] * len(ids),
        "company_code": ["TKN_" + "f" * 32] * len(ids),
        "reference_document": ["TKN_" + "e" * 32] * len(ids),
        "total_absolute_value": [1000.0] * len(ids),
    })


def test_safe_minimal_export_no_financial_or_company_data():
    matrix = sheet.build_review_sheet_matrix(alerts(), [])
    assert matrix[0] == list(sheet.SHEET_COLUMNS)
    assert len(matrix) == 3
    assert all(len(row) == 8 for row in matrix)
    assert matrix[1][4] == "PENDING" and matrix[1][-1] == "PENDING"
    assert "company_code" not in matrix[0]
    assert "reference_document" not in matrix[0]
    assert "total_absolute_value" not in matrix[0]
    assert "TKN_" not in str(matrix)
    assert "1000.0" not in str(matrix)


def test_no_input_mutation():
    data = alerts()
    before = data.copy(deep=True)
    sheet.build_review_sheet_matrix(data, [])
    pd.testing.assert_frame_equal(data, before)


def test_empty_rows_produce_only_header():
    matrix = sheet.build_review_sheet_matrix(alerts(()), [])
    assert len(matrix) == 1
    assert sheet.validate_review_sheet_matrix(matrix) == {}


def test_sorted_by_period_and_alert_id():
    data = alerts((C, A, B))
    data.loc[0, "period_key"] = "2026-08"
    matrix = sheet.build_review_sheet_matrix(data, [])
    assert [row[0] for row in matrix[1:]] == [C, A, B]


def test_events_are_reflected_without_changing_default_new_alert():
    data = alerts()
    events = workflow.append_review_event(
        [], data, alert_id=A, new_status="IN_REVIEW", expected_revision=0,
        reviewer_token="REV_" + "f" * 32,
    )
    matrix = sheet.build_review_sheet_matrix(data, events)
    assert matrix[1][4] == "IN_REVIEW"
    assert matrix[1][5] == 1
    assert matrix[1][6].endswith("Z")
    assert matrix[1][7] == "IN_REVIEW"
    assert matrix[2][4] == "PENDING" and matrix[2][5] == 0


def test_status_dropdown_payload_matches_only_edit_column():
    request = sheet.build_status_dropdown_request(7, max_alerts=33)["setDataValidation"]
    assert request["range"] == {
        "sheetId": 7, "startRowIndex": 1, "endRowIndex": 34,
        "startColumnIndex": 7, "endColumnIndex": 8,
    }
    assert request["rule"]["strict"] is True
    values = request["rule"]["condition"]["values"]
    assert [x["userEnteredValue"] for x in values] == list(workflow.REVIEW_STATUSES)


@pytest.mark.parametrize("sheet_id", [-1, "7", None, True, 7.2])
def test_invalid_sheet_id_rejected(sheet_id):
    with pytest.raises(ValueError):
        sheet.build_status_dropdown_request(sheet_id)


@pytest.mark.parametrize("size", [-1, 0, 5001, "5", 1.2, True])
def test_invalid_capacity_rejected(size):
    with pytest.raises(ValueError):
        sheet.build_status_dropdown_request(0, max_alerts=size)


@pytest.mark.parametrize("col,value", [
    (0, "ORIGINAL_COMPANY"), (1, "2026-13"), (2, "URGENT"),
    (3, '=HYPERLINK("x","y")'), (4, "APPROVED"), (5, -1),
    (5, True), (5, float("nan")), (5, 1.5),
    (6, "today"), (7, "=IMPORTDATA(x)"), (7, "CORRECTED_NOW"),
])
def test_cell_corruption_rejected(col, value):
    matrix = sheet.build_review_sheet_matrix(alerts(), [])
    matrix[1][col] = value
    with pytest.raises(ValueError):
        sheet.validate_review_sheet_matrix(matrix)


def test_invalid_initial_revision_timestamp_rejected():
    matrix = sheet.build_review_sheet_matrix(alerts(), [])
    matrix[1][6] = "2026-11-18T10:00:00Z"
    with pytest.raises(ValueError):
        sheet.validate_review_sheet_matrix(matrix)


def test_duplicate_id_rejected():
    matrix = sheet.build_review_sheet_matrix(alerts(), [])
    matrix[2][0] = matrix[1][0]
    with pytest.raises(ValueError, match="duplicado"):
        sheet.validate_review_sheet_matrix(matrix)


def test_extra_column_rejected():
    matrix = sheet.build_review_sheet_matrix(alerts(), [])
    matrix[1].append("SENSITIVE_DATA")
    with pytest.raises(ValueError):
        sheet.validate_review_sheet_matrix(matrix)


def test_wrong_header_rejected():
    matrix = sheet.build_review_sheet_matrix(alerts(), [])
    matrix[0][0] = "sensitive_id"
    with pytest.raises(ValueError, match="Cabecalho"):
        sheet.validate_review_sheet_matrix(matrix)


def test_float_integer_from_google_is_normalized():
    matrix = sheet.build_review_sheet_matrix(alerts(), [])
    matrix[1][5] = 0.0
    assert sheet.validate_review_sheet_matrix(matrix)[A][5] == 0


def test_count_over_limit_rejected():
    frame = pd.concat([alerts((A,))] * (sheet.MAX_ALERTS + 1), ignore_index=True)
    with pytest.raises(ValueError, match="limite"):
        sheet.build_review_sheet_matrix(frame, [])


@pytest.mark.parametrize("missing", [None, float("nan"), pd.NA, pd.NaT])
def test_missing_timestamps_are_normalized_for_pending_alerts(monkeypatch, missing):
    data = alerts()
    events = workflow.append_review_event(
        [], data, alert_id=A, new_status="IN_REVIEW", expected_revision=0,
        reviewer_token="REV_" + "f" * 32,
    )
    original_projection = sheet.build_review_projection

    def projection_with_missing_timestamp(*args, **kwargs):
        projection = original_projection(*args, **kwargs)
        projection.loc[projection["review_revision"] == 0, "review_updated_at_utc"] = missing
        return projection

    monkeypatch.setattr(sheet, "build_review_projection", projection_with_missing_timestamp)
    matrix = sheet.build_review_sheet_matrix(data, events)
    assert matrix[1][4] == "IN_REVIEW"
    assert matrix[1][5] == 1
    assert matrix[1][6].endswith("Z")
    assert matrix[2][4] == "PENDING"
    assert matrix[2][5] == 0
    assert matrix[2][6] == ""


def test_missing_timestamp_for_completed_event_is_still_blocked(monkeypatch):
    data = alerts()
    events = workflow.append_review_event(
        [], data, alert_id=A, new_status="IN_REVIEW", expected_revision=0,
        reviewer_token="REV_" + "f" * 32,
    )
    original_projection = sheet.build_review_projection

    def projection_with_missing_event_timestamp(*args, **kwargs):
        projection = original_projection(*args, **kwargs)
        projection.loc[projection["alert_id"] == A, "review_updated_at_utc"] = float("nan")
        return projection

    monkeypatch.setattr(sheet, "build_review_projection", projection_with_missing_event_timestamp)
    with pytest.raises(ValueError, match="Horario de revisao UTC invalido"):
        sheet.build_review_sheet_matrix(data, events)
