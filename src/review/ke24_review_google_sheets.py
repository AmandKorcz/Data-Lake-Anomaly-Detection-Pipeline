from __future__ import annotations

import os
import re

from src.review.ke24_review_context import CONTEXT_COLUMNS, CONTEXT_HEADERS, CONTEXT_SHEET_TITLE

from src.review.ke24_review_sheet import (
    MAX_ALERTS, SHEET_COLUMNS, SHEET_TITLE,
    build_status_dropdown_request, validate_review_sheet_matrix,
)

SPREADSHEET_ENV = "KE24_REVIEW_SPREADSHEET_ID"
WORKBOOK_TITLE = "KE24 - Fila Operacional de Revisao (Privada)"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{10,}\Z")
_META_FIELDS = (
    "spreadsheetId,sheets(properties(sheetId,title,gridProperties),"
    "protectedRanges(protectedRangeId,range,description,warningOnly,editors,unprotectedRanges))"
)


def get_spreadsheet_id(environ=None):
    if environ is None:
        from src.config.ke24_local_settings import load_ke24_local_settings
        load_ke24_local_settings()
        env = os.environ
    else:
        env = environ
    value = str(env.get(SPREADSHEET_ENV, "")).strip()
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError(f"Configure {SPREADSHEET_ENV} somente em seu ambiente local.")
    # Never send alert rows to the old technical-only spreadsheet.
    if value == str(env.get("KE24_LOOKER_SPREADSHEET_ID", "")).strip():
        raise ValueError("A fila de revisao deve usar uma planilha diferente da tecnica.")
    return value


def _spreadsheet_metadata(sheets_service, spreadsheet_id):
    return sheets_service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields=_META_FIELDS,
    ).execute()


def _target_metadata(drive_service, spreadsheet_id):
    return drive_service.files().get(
        fileId=spreadsheet_id,
        fields="id,mimeType,shared,trashed,capabilities(canEdit),owners(emailAddress)",
    ).execute()


def _owner_email(target):
    owners = target.get("owners")
    if not isinstance(owners, list) or len(owners) != 1:
        raise RuntimeError("Propriedade da planilha nao verificavel; operacao bloqueada.")
    owner = owners[0].get("emailAddress")
    if not isinstance(owner, str) or "@" not in owner:
        raise RuntimeError("Proprietario da planilha nao verificavel.")
    return owner.lower()


def verify_private_review_sheet(sheets_service, drive_service, spreadsheet_id):
    if not _ID_PATTERN.fullmatch(str(spreadsheet_id)):
        raise ValueError("Identificador da planilha invalido.")
    target = _target_metadata(drive_service, spreadsheet_id)
    if (target.get("id") != spreadsheet_id
            or target.get("mimeType") != SHEET_MIME
            or target.get("trashed") is not False
            or target.get("shared") is not False
            or target.get("capabilities", {}).get("canEdit") is not True):
        raise RuntimeError("Fila invalida, compartilhada ou sem permissao de edicao; bloqueado.")
    owner = _owner_email(target)
    book = _spreadsheet_metadata(sheets_service, spreadsheet_id)
    tabs = book.get("sheets", [])
    if book.get("spreadsheetId") != spreadsheet_id or len(tabs) not in (1, 2):
        raise RuntimeError("A fila exige uma ou duas abas estritamente controladas.")
    titles = [tab.get("properties", {}).get("title") for tab in tabs]
    if len(set(titles)) != len(titles) or SHEET_TITLE not in titles:
        raise RuntimeError("Aba operacional ausente ou duplicada.")
    if len(tabs) == 2:
        if CONTEXT_SHEET_TITLE not in titles:
            raise RuntimeError("Aba adicional nao autorizada; reconciliacao necessaria.")
        _verify_private_context_tab(tabs[titles.index(CONTEXT_SHEET_TITLE)], owner)
    sheet = tabs[titles.index(SHEET_TITLE)]
    props = sheet.get("properties", {})
    sheet_id = props.get("sheetId")
    grid = props.get("gridProperties", {})
    if (type(sheet_id) is not int or sheet_id < 0
            or props.get("title") != SHEET_TITLE
            or grid.get("columnCount") != len(SHEET_COLUMNS)
            or grid.get("rowCount", 0) < MAX_ALERTS + 1
            or grid.get("frozenRowCount") != 1):
        raise RuntimeError("Contrato estrutural da planilha de revisao divergente.")
    protections = sheet.get("protectedRanges", [])
    if len(protections) != 1:
        raise RuntimeError("Protecao das colunas tecnicas nao verificavel.")
    protection = protections[0]
    expected_range = {
        "sheetId": sheet_id,
        "startColumnIndex": 0,
        "endColumnIndex": len(SHEET_COLUMNS) - 1,
    }
    region = protection.get("range", {})
    if (protection.get("warningOnly", False) is not False
            or any(region.get(k, 0) != v for k, v in expected_range.items())
            or region.get("startRowIndex", 0) != 0
            or "endRowIndex" in region
            or "protectedRangeId" not in protection
            or protection.get("unprotectedRanges")):
        raise RuntimeError("Protecao obrigatoria das colunas A:G alterada.")
    editors = protection.get("editors", {})
    allowed = {str(user).lower() for user in editors.get("users", [])}
    if (allowed != {owner} or editors.get("groups", [])
            or editors.get("domainUsersCanEdit", False)):
        raise RuntimeError("Permissao de edicao das colunas tecnicas incompatível.")
    return sheet_id


def create_private_review_sheet(sheets_service, drive_service):
    created = sheets_service.spreadsheets().create(
        body={
            "properties": {"title": WORKBOOK_TITLE},
            "sheets": [{"properties": {
                "title": SHEET_TITLE,
                "gridProperties": {
                    "rowCount": MAX_ALERTS + 1,
                    "columnCount": len(SHEET_COLUMNS),
                    "frozenRowCount": 1,
                },
            }}],
        },
        fields="spreadsheetId,spreadsheetUrl,sheets(properties(sheetId,title))",
    ).execute()
    spreadsheet_id = created.get("spreadsheetId")
    if not isinstance(spreadsheet_id, str) or not _ID_PATTERN.fullmatch(spreadsheet_id):
        raise RuntimeError("Criacao de planilha indeterminada; confira seu Drive.")
    return created


def build_initialization_requests(sheet_id, owner_email):
    if type(sheet_id) is not int or sheet_id < 0:
        raise ValueError("Aba sem identificador valido.")
    if not isinstance(owner_email, str) or "@" not in owner_email:
        raise ValueError("Proprietario sem identificacao verificavel.")
    header = {"values": [
        {"userEnteredValue": {"stringValue": name}}
        for name in SHEET_COLUMNS
    ]}
    return [
        {"updateCells": {
            "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0},
            "rows": [header], "fields": "userEnteredValue",
        }},
        build_status_dropdown_request(sheet_id),
        {"addProtectedRange": {"protectedRange": {
            "range": {"sheetId": sheet_id,
                      "startColumnIndex": 0,
                      "endColumnIndex": len(SHEET_COLUMNS) - 1},
            "description": "KE24 - Colunas tecnicas: somente o proprietario",
            "warningOnly": False,
            "editors": {"users": [owner_email.lower()],
                        "groups": [], "domainUsersCanEdit": False},
        }}},
    ]


def initialize_private_review_sheet(sheets_service, drive_service, created):
    spreadsheet_id = created["spreadsheetId"]
    target = _target_metadata(drive_service, spreadsheet_id)
    if target.get("shared") is not False:
        raise RuntimeError("Planilha criada ja compartilhada; operacao bloqueada.")
    owner_email = _owner_email(target)
    sheets = created.get("sheets", [])
    if len(sheets) != 1:
        raise RuntimeError("Criacao sem aba unica; reconciliacao manual necessaria.")
    sheet_id = sheets[0].get("properties", {}).get("sheetId")
    requests = build_initialization_requests(sheet_id, owner_email)
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests, "includeSpreadsheetInResponse": False},
    ).execute()
    verified_id = verify_private_review_sheet(sheets_service, drive_service, spreadsheet_id)
    if verified_id != sheet_id:
        raise RuntimeError("Aba configurada mudou; revisar antes de usar.")
    rows = read_review_sheet_matrix(sheets_service, spreadsheet_id)
    if rows != [list(SHEET_COLUMNS)]:
        raise RuntimeError("Planilha criada tem conteudo inesperado.")
    return verified_id


def read_review_sheet_matrix(sheets_service, spreadsheet_id):
    raw = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_TITLE}'!A1:H{MAX_ALERTS + 1}",
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute().get("values", [])
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("Planilha vazia ou sem cabecalho verificado.")
    normalized = validate_review_sheet_matrix(raw)
    # Keep physical order while converting numeric revisions to canonical int.
    return [list(SHEET_COLUMNS), *(normalized[row[0]] for row in raw[1:])]


def _typed_row(row):
    if len(row) != len(SHEET_COLUMNS):
        raise ValueError("Linha de alerta invalida.")
    values = []
    for index, value in enumerate(row):
        if index == 5:
            values.append({"userEnteredValue": {"numberValue": int(value)}})
        else:
            values.append({"userEnteredValue": {"stringValue": str(value)}})
    return {"values": values}


def append_new_review_alerts(sheets_service, spreadsheet_id, sheet_id, rows):
    if not rows:
        return
    verify_rows = [list(SHEET_COLUMNS), *[list(row) for row in rows]]
    validate_review_sheet_matrix(verify_rows)
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"appendCells": {
            "sheetId": sheet_id,
            "rows": [_typed_row(row) for row in rows],
            "fields": "userEnteredValue",
        }}], "includeSpreadsheetInResponse": False},
    ).execute()


def _verify_private_context_tab(tab, owner_email):
    props = tab.get("properties", {})
    grid = props.get("gridProperties", {})
    tab_id = props.get("sheetId")
    if (type(tab_id) is not int or tab_id < 0
            or props.get("title") != CONTEXT_SHEET_TITLE
            or grid.get("columnCount") != len(CONTEXT_COLUMNS)
            or grid.get("rowCount", 0) < MAX_ALERTS + 1
            or grid.get("frozenRowCount") != 1):
        raise RuntimeError("Estrutura da aba de contexto divergente.")
    protections = tab.get("protectedRanges", [])
    if len(protections) != 1:
        raise RuntimeError("Contexto sem protecao exclusiva do proprietario.")
    protection = protections[0]
    region = protection.get("range", {})
    if ("protectedRangeId" not in protection
            or protection.get("warningOnly", False) is not False
            or region.get("sheetId") != tab_id
            or any(region.get(k, 0) != 0 for k in ("startRowIndex", "startColumnIndex"))
            or "endRowIndex" in region or "endColumnIndex" in region
            or protection.get("unprotectedRanges")):
        raise RuntimeError("Protecao integral da aba de contexto alterada.")
    editors = protection.get("editors", {})
    users = {str(u).lower() for u in editors.get("users", [])}
    if (users != {owner_email} or editors.get("groups", [])
            or editors.get("domainUsersCanEdit", False)):
        raise RuntimeError("Permissao da aba de contexto incompatível.")
    return tab_id


def get_context_sheet_id(sheets_service, drive_service, spreadsheet_id):
    """Verify both tabs, returning None if the second tab has not been created."""
    verify_private_review_sheet(sheets_service, drive_service, spreadsheet_id)
    meta = _spreadsheet_metadata(sheets_service, spreadsheet_id)
    for tab in meta["sheets"]:
        if tab["properties"]["title"] == CONTEXT_SHEET_TITLE:
            return tab["properties"]["sheetId"]
    return None


def read_private_context_matrix(sheets_service, spreadsheet_id):
    """Read only the context tab; return its exact header and rows, no truncation."""
    from src.review.ke24_review_context_sync import validate_context_matrix
    response = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{CONTEXT_SHEET_TITLE}'!A1:K{MAX_ALERTS + 1}",
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute()
    values = response.get("values", [])
    validate_context_matrix(values)
    return [list(row) for row in values]


def build_context_tab_creation_requests(context_id, owner_email):
    if type(context_id) is not int or context_id < 0:
        raise ValueError("ID de nova aba de contexto invalido.")
    if not isinstance(owner_email, str) or "@" not in owner_email:
        raise ValueError("Proprietario de contexto invalido.")
    return [
        {"addSheet": {"properties": {
            "sheetId": context_id,
            "title": CONTEXT_SHEET_TITLE,
            "gridProperties": {
                "rowCount": MAX_ALERTS + 1,
                "columnCount": len(CONTEXT_COLUMNS),
                "frozenRowCount": 1,
            },
        }}},
        {"updateCells": {
            "start": {"sheetId": context_id, "rowIndex": 0, "columnIndex": 0},
            "rows": [{"values": [
                {"userEnteredValue": {"stringValue": item}}
                for item in CONTEXT_HEADERS
            ]}],
            "fields": "userEnteredValue",
        }},
        {"addProtectedRange": {"protectedRange": {
            "range": {"sheetId": context_id},
            "warningOnly": False,
            "description": "KE24 - Contexto confidencial somente ao proprietario",
            "editors": {"users": [owner_email.lower()],
                        "groups": [], "domainUsersCanEdit": False},
        }}},
        {"setBasicFilter": {"filter": {
            "range": {"sheetId": context_id, "startRowIndex": 0,
                      "endRowIndex": MAX_ALERTS + 1,
                      "startColumnIndex": 0,
                      "endColumnIndex": len(CONTEXT_COLUMNS)},
        }}},
    ]


def create_private_context_tab(sheets_service, drive_service, spreadsheet_id):
    """Explicit one-time creation of a second private read-only tab."""
    if get_context_sheet_id(sheets_service, drive_service, spreadsheet_id) is not None:
        raise RuntimeError("Aba de contexto ja existe; nao criar duplicada.")
    meta = _spreadsheet_metadata(sheets_service, spreadsheet_id)
    used = {s["properties"]["sheetId"] for s in meta["sheets"]}
    new_id = next(i for i in range(1_000_001, 1_000_100) if i not in used)
    owner = _owner_email(_target_metadata(drive_service, spreadsheet_id))
    try:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": build_context_tab_creation_requests(new_id, owner),
                  "includeSpreadsheetInResponse": False},
        ).execute()
    except Exception:
        raise RuntimeError("Criacao de contexto indeterminada; nao repetir sem diagnostico.") from None
    actual_id = get_context_sheet_id(sheets_service, drive_service, spreadsheet_id)
    if actual_id != new_id or read_private_context_matrix(
            sheets_service, spreadsheet_id) != [list(CONTEXT_HEADERS)]:
        raise RuntimeError("Configuracao do contexto nao confirmada; parar e diagnosticar.")
    return new_id


def append_private_context_rows(sheets_service, spreadsheet_id, sheet_id, rows):
    from src.review.ke24_review_context_sync import validate_context_matrix
    if not rows:
        return
    validate_context_matrix([list(CONTEXT_HEADERS), *[list(row) for row in rows]])
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"appendCells": {
            "sheetId": sheet_id,
            "rows": [{"values": [
                {"userEnteredValue": {"stringValue": str(value)}}
                for value in row
            ]} for row in rows],
            "fields": "userEnteredValue",
        }}], "includeSpreadsheetInResponse": False},
    ).execute()
