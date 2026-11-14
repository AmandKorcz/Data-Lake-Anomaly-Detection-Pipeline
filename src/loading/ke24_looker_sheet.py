import math
import os
import re

import pandas as pd

from src.alerts.build_ke24_pretrained_outputs import build_pretrained_dashboard
from src.loading.ke24_drive_alert_history import validate_history

SHEET_ID_ENV = "KE24_LOOKER_SPREADSHEET_ID"
SHEET_TITLE = "ke24_alerts"
WORKBOOK_TITLE = "KE24 - Alertas Protegidos - TCC"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
MAX_ROWS = 5000  # limite intencional para revisar volume, custo e privacidade

EXPORT_COLUMNS = [
    "alert_id",
    "company_code",
    "period_key",
    "detection_date",
    "alert_level",
    "classification_status",
    "investigation_status",
    "stability_rate",
    "materiality_percentile",
    "dominant_measure",
    "model_version",
    "alert_count",
]
NUMBER_COLUMNS = {"stability_rate", "materiality_percentile", "alert_count"}


def get_spreadsheet_id():
    spreadsheet_id = os.getenv(SHEET_ID_ENV, "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", spreadsheet_id):
        raise ValueError(f"Configure {SHEET_ID_ENV} com o ID da planilha criada pelo aplicativo.")
    return spreadsheet_id


def make_export(history, policy):
    if not isinstance(history, pd.DataFrame) or history.empty:
        raise ValueError("Historico remoto vazio; nao preparar uma planilha vazia.")
    validate_history(history, policy)
    dashboard = build_pretrained_dashboard(history, policy)
    if not set(EXPORT_COLUMNS).issubset(dashboard.columns):
        raise ValueError("Contrato do dashboard incompleto.")
    if len(dashboard) > MAX_ROWS:
        raise ValueError("Volume excede limite da exportacao; revisar a arquitetura antes de publicar.")
    if dashboard["alert_id"].duplicated().any():
        raise ValueError("IDs de alerta duplicados.")
    if not dashboard["alert_count"].eq(1).all():
        raise ValueError("Uma linha deve representar exatamente um alerta.")
    return dashboard.loc[:, EXPORT_COLUMNS].sort_values(
        ["period_key", "alert_id"], kind="stable"
    ).reset_index(drop=True)


def _cell_value(value, column):
    if column in NUMBER_COLUMNS:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Metrica invalida para Sheets: {column}") from exc
        if not math.isfinite(number):
            raise ValueError(f"Metrica nao finita para Sheets: {column}")
        return int(number) if column == "alert_count" else number
    if pd.isna(value):
        raise ValueError(f"Campo obrigatorio nao preenchido: {column}")
    result = str(value)
    if not result.strip():
        raise ValueError(f"Campo vazio no dashboard: {column}")
    return result


def build_matrix(history, policy):
    export = make_export(history, policy)
    rows = [list(EXPORT_COLUMNS)]
    for tup in export.itertuples(index=False, name=None):
        rows.append([_cell_value(v, c) for v, c in zip(tup, EXPORT_COLUMNS)])
    if len(rows) != len(history) + 1:
        raise RuntimeError("Quantidade de linhas de exportacao divergente.")
    return rows


def _letter(index):
    """Indice 1-based para letra A1."""
    s = ""
    while index:
        index, rem = divmod(index - 1, 26)
        s = chr(65 + rem) + s
    return s


def _cells_equal(a, b):
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        if isinstance(b, (int, float)) and not isinstance(b, bool):
            return math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-9)
    return a == b and type(a) is type(b)


def _row_equal(a, b):
    return len(a) == len(b) and all(_cells_equal(x, y) for x, y in zip(a, b))


def check_existing_sheet(actual, desired):
    if not isinstance(actual, list):
        raise RuntimeError("Formato de leitura do Google Sheets invalido.")
    if not actual:
        return 0
    if actual[0] != list(EXPORT_COLUMNS):
        raise RuntimeError("Cabecalho da planilha nao corresponde ao contrato KE24.")
    if len(actual) > MAX_ROWS + 1:
        raise RuntimeError("Planilha excede o limite de linhas autorizado.")
    expected_by_id = {row[0]: row for row in desired[1:]}
    seen = set()
    for row in actual[1:]:
        if len(row) != len(EXPORT_COLUMNS):
            raise RuntimeError("Linha incompleta ou alterada na planilha.")
        alert_id = row[0]
        if alert_id in seen:
            raise RuntimeError("Alerta duplicado na planilha.")
        seen.add(alert_id)
        expected_row = expected_by_id.get(alert_id)
        if expected_row is None:
            raise RuntimeError("A planilha contem alerta inexistente no historico protegido.")
        if not _row_equal(row, expected_row):
            raise RuntimeError("Registro da planilha alterado; sobrescrita bloqueada.")
    return len(seen)


def create_empty_sheet(sheets_service):
    result = sheets_service.spreadsheets().create(
        body={
            "properties": {"title": WORKBOOK_TITLE},
            "sheets": [{
                "properties": {
                    "title": SHEET_TITLE,
                    "gridProperties": {
                        "rowCount": 1000,
                        "columnCount": len(EXPORT_COLUMNS),
                        "frozenRowCount": 1,
                    },
                },
            }],
        },
        fields="spreadsheetId,spreadsheetUrl,sheets(properties(sheetId,title))",
    ).execute()
    if not result.get("spreadsheetId"):
        raise RuntimeError("Criacao da planilha indeterminada; confira o Drive antes de repetir.")
    return result


def inspect_sheet(sheets_service, drive_service, spreadsheet_id):
    target = drive_service.files().get(
        fileId=spreadsheet_id,
        fields="id,mimeType,shared,trashed,capabilities(canEdit)",
    ).execute()
    if (target.get("mimeType") != SHEET_MIME
            or target.get("trashed") is True
            or target.get("capabilities", {}).get("canEdit") is not True):
        raise RuntimeError("Planilha invalida ou sem acesso de edicao pelo aplicativo.")
    if target.get("shared") is not False:
        raise RuntimeError("Planilha compartilhada ou privacidade nao verificavel; bloqueado.")
    metadata = sheets_service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="spreadsheetId,sheets(properties(sheetId,title,gridProperties))",
    ).execute()
    sheets = metadata.get("sheets", [])
    if len(sheets) != 1 or sheets[0].get("properties", {}).get("title") != SHEET_TITLE:
        raise RuntimeError("Destino nao corresponde a uma planilha KE24 dedicada.")
    sheet = sheets[0]["properties"]
    grid = sheet.get("gridProperties", {})
    if grid.get("columnCount") != len(EXPORT_COLUMNS) or not isinstance(sheet.get("sheetId"), int):
        raise RuntimeError("Formato da aba nao corresponde ao contrato KE24.")
    return sheet


def read_sheet_matrix(sheets_service, spreadsheet_id):
    value_range = f"'{SHEET_TITLE}'!A1:{_letter(len(EXPORT_COLUMNS))}"
    return sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=value_range,
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute().get("values", [])


def _typed_cell(value, column):
    if column in NUMBER_COLUMNS:
        return {"userEnteredValue": {"numberValue": float(value)}}
    return {"userEnteredValue": {"stringValue": str(value)}}


def build_atomic_update(desired, sheet):
    rows = [{"values": [
        {"userEnteredValue": {"stringValue": str(value)}}
        if row_idx == 0 else _typed_cell(value, EXPORT_COLUMNS[col_idx])
        for col_idx, value in enumerate(row)
    ]} for row_idx, row in enumerate(desired)]
    props = sheet["gridProperties"]
    return {
        "requests": [
            {"updateSheetProperties": {
                "properties": {
                    "sheetId": sheet["sheetId"],
                    "gridProperties": {
                        "rowCount": max(props.get("rowCount", 1), len(rows)),
                        "columnCount": len(EXPORT_COLUMNS),
                        "frozenRowCount": 1,
                    },
                },
                "fields": "gridProperties",
            }},
            {"updateCells": {
                "start": {"sheetId": sheet["sheetId"], "rowIndex": 0, "columnIndex": 0},
                "rows": rows,
                "fields": "userEnteredValue",
            }},
        ],
        "includeSpreadsheetInResponse": False,
    }


def publish_sheet(sheets_service, drive_service, spreadsheet_id, desired, previous_values=None):
    sheet = inspect_sheet(sheets_service, drive_service, spreadsheet_id)
    observed = read_sheet_matrix(sheets_service, spreadsheet_id)
    check_existing_sheet(observed, desired)
    if previous_values is not None and observed != previous_values:
        raise RuntimeError("Planilha mudou durante a verificacao; execute nova previa.")
    if len(observed) == len(desired) and all(_row_equal(x, y) for x, y in zip(observed, desired)):
        return {"published": False, "rows": len(desired) - 1}
    body = build_atomic_update(desired, sheet)
    try:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body=body,
        ).execute()
        verified = read_sheet_matrix(sheets_service, spreadsheet_id)
        if len(verified) != len(desired) or not all(
            _row_equal(x, y) for x, y in zip(verified, desired)
        ):
            raise RuntimeError("Verificacao pos-escrita divergente.")
    except Exception as exc:
        raise RuntimeError(
            "Sincronizacao do Sheets incompleta ou indeterminada: NAO repita "
            "automaticamente. Verifique a planilha e reconcilie antes de tentar de novo."
        ) from exc
    return {"published": True, "rows": len(desired) - 1}
