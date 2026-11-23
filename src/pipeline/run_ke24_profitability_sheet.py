import argparse
from datetime import date
from itertools import zip_longest
import math
import os
from pathlib import Path
import re

from google.cloud import bigquery
from googleapiclient.discovery import build

from src.config.ke24_local_settings import ID_PATTERN, PROFITABILITY_SHEET_KEY
from src.ingestion.google_drive_client import authenticate_write
from src.loading.ke24_looker_sheet import create_empty_sheet, inspect_sheet, read_sheet_matrix
from src.loading.load_ke24_bigquery import BQ_LOCATION, GCP_PROJECT
from src.privacy.privacy_runtime import load_privacy_runtime
from src.privacy.validate_protected_dataset import TOKEN_PATTERN
from src.review.ke24_review_company_aliases import load_private_company_aliases


SQL_FILE = Path(__file__).resolve().parents[2] / "sql" / "ke24_rentabilidade_previa.sql"
WORKBOOK_TITLE = "KE24 - Rentabilidade"
SHEET_TITLE = "rentabilidade_ke24"
DIMENSIONS = ("company_code", "period_key", "currency_type", "currency")
METRICS = (
    "total_lancamentos", "total_revenue", "gross_sales", "taxes_over_sales",
    "sales_allowances", "net_sales", "product_cost", "material_cost",
    "direct_labor", "variable_overhead", "doh_ind_labor", "doh",
    "scrap_obsolescence", "sales_variable_cost", "contribution_margin",
    "fixed_overhead", "other_fixed_cost", "gross_profit", "operations_expenses",
    "industrial_op", "other_operating_income_expense",
    "other_financial_income_expense", "income_taxes",
)
LEGACY_HEADERS = [*DIMENSIONS[:2], "period_date", *DIMENSIONS[2:], *METRICS]
DETAIL_DIMENSIONS = ("period", "calendar_year", "plant", "profit_center", "copa_event_type")
HEADERS = [*LEGACY_HEADERS, *DETAIL_DIMENSIONS]
EXCEL_EPOCH = date(1899, 12, 30)
SHEET_OPTIONS = {"sheet_title": SHEET_TITLE, "column_count": len(HEADERS)}


def get_profitability_spreadsheet_id():
    value = os.getenv(PROFITABILITY_SHEET_KEY, "").strip()
    if not ID_PATTERN.fullmatch(value):
        raise ValueError(f"Configure {PROFITABILITY_SHEET_KEY} com o ID da planilha criada.")
    reserved = (
        os.getenv("KE24_LOOKER_SPREADSHEET_ID", "").strip(),
        os.getenv("KE24_REVIEW_SPREADSHEET_ID", "").strip(),
    )
    if value in reserved:
        raise ValueError("Use uma planilha dedicada à rentabilidade.")
    return value


def build_profitability_matrix(rows, company_aliases):
    matrix = [HEADERS]
    seen = set()
    for row in rows:
        dimensions = tuple(row[column] for column in DIMENSIONS)
        if any(not isinstance(value, str) or not value.strip() for value in dimensions):
            raise ValueError("Dimensão de rentabilidade ausente ou inválida.")
        company, period_key, currency_type, currency = dimensions
        if not re.fullmatch(TOKEN_PATTERN, company):
            raise ValueError("Empresa sem identificador protegido. Publicação bloqueada.")
        company_label = company_aliases.get(company)
        if company_label is None:
            raise ValueError(
                "Empresa sem alias cadastrado em company_aliases.json. "
                "Complete o mapeamento privado antes de publicar."
            )
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", period_key):
            raise ValueError("Competência inválida para publicação.")
        period_date = date.fromisoformat(f"{period_key}-01")
        detail = tuple(row[column] for column in DETAIL_DIMENSIONS)
        period, calendar_year, plant, profit_center, event_type = detail
        for value, expected in ((period, period_date.month), (calendar_year, period_date.year)):
            if value is not None and (type(value) is not int or value != expected):
                raise ValueError("Período ou ano calendário divergente da competência.")
        for value in (plant, profit_center):
            if value is not None and (
                not isinstance(value, str) or not re.fullmatch(TOKEN_PATTERN, value)
            ):
                raise ValueError("Planta ou centro de lucro sem identificador protegido.")
        if event_type is not None and not isinstance(event_type, str):
            raise ValueError("Tipo de evento CO-PA inválido.")

        key = (*dimensions, *detail)
        if key in seen:
            raise ValueError("Dimensões de rentabilidade duplicadas no resultado.")
        seen.add(key)

        serial = (period_date - EXCEL_EPOCH).days
        values = [float(row[column]) for column in METRICS]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Métrica não finita no resultado do BigQuery.")
        if values[0] <= 0 or not values[0].is_integer():
            raise ValueError("Contagem de lançamentos inválida.")
        matrix.append([company_label, period_key, serial, currency_type, currency, *values, *detail])

    if len(matrix) == 1:
        raise ValueError("Consulta sem resultados. A planilha foi preservada.")
    return matrix


def _same_matrix(actual, desired):
    if len(actual) != len(desired):
        return False
    for current, target in zip(actual, desired):
        if len(current) > len(target):
            return False
        # O Sheets omite células vazias no fim de cada linha.
        for cell, expected in zip_longest(current, target):
            if expected is None:
                if cell not in (None, ""):
                    return False
            elif isinstance(expected, str):
                if cell != expected:
                    return False
            elif type(cell) not in (int, float) or not math.isclose(
                cell, expected, rel_tol=1e-14, abs_tol=1e-9
            ):
                return False
    return True


def publish_profitability_matrix(sheets, drive, spreadsheet_id, desired):
    target = inspect_sheet(
        sheets, drive, spreadsheet_id, **SHEET_OPTIONS, legacy_column_count=len(LEGACY_HEADERS),
    )
    column_count = target["gridProperties"]["columnCount"]
    needs_expansion = column_count == len(LEGACY_HEADERS)
    expected_header = LEGACY_HEADERS if needs_expansion else HEADERS
    existing = read_sheet_matrix(
        sheets, spreadsheet_id, sheet_title=SHEET_TITLE, column_count=column_count,
    )
    if existing and existing[0] != expected_header:
        raise ValueError("Cabeçalho divergente. Use a aba dedicada criada pelo comando.")
    if _same_matrix(existing, desired):
        return False

    sheet_id = target["sheetId"]
    row_count = target["gridProperties"]["rowCount"]
    rows = [
        {"values": [
            {} if value is None else {"userEnteredValue": (
                {"stringValue": value} if isinstance(value, str) else {"numberValue": value}
            )}
            for value in row
        ]}
        for row in desired
    ]
    requests = []
    if needs_expansion:
        requests.append({"appendDimension": {
            "sheetId": sheet_id, "dimension": "COLUMNS", "length": len(DETAIL_DIMENSIONS),
        }})
    if len(rows) > row_count:
        requests.append({"appendDimension": {
            "sheetId": sheet_id, "dimension": "ROWS", "length": len(rows) - row_count,
        }})
    requests.extend([
        {"updateCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": max(len(existing), len(rows)),
                "startColumnIndex": 0,
                "endColumnIndex": len(HEADERS),
            },
            "rows": rows,
            "fields": "userEnteredValue",
        }},
        {"repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "endRowIndex": len(rows),
                "startColumnIndex": 2,
                "endColumnIndex": 3,
            },
            "cell": {"userEnteredFormat": {
                "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"},
            }},
            "fields": "userEnteredFormat.numberFormat",
        }},
    ])
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests}
    ).execute()
    verified = read_sheet_matrix(sheets, spreadsheet_id, **SHEET_OPTIONS)
    if not _same_matrix(verified, desired):
        raise RuntimeError("Publicação realizada, mas a releitura do Sheets divergiu.")
    return True


def sync_profitability(client, *, spreadsheet_id=None):
    _, _, key = load_privacy_runtime()
    company_aliases = load_private_company_aliases(key)
    sql = SQL_FILE.read_text(encoding="utf-8")
    rows = client.query(sql, location=BQ_LOCATION).result()
    desired = build_profitability_matrix(rows, company_aliases)
    print(f"Grupos de rentabilidade validados no BigQuery: {len(desired) - 1}")
    if spreadsheet_id is None:
        print("Prévia concluída. Use --publish para atualizar a planilha.")
        return

    credential = authenticate_write()
    sheets = build("sheets", "v4", credentials=credential, cache_discovery=False)
    drive = build("drive", "v3", credentials=credential, cache_discovery=False)
    if publish_profitability_matrix(sheets, drive, spreadsheet_id, desired):
        print("Planilha de rentabilidade atualizada e conferida.")
    else:
        print("Planilha de rentabilidade já está atualizada.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="BigQuery → Google Sheets de rentabilidade KE24.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--create-sheet", action="store_true", help="Cria a planilha dedicada vazia.")
    modes.add_argument("--publish", action="store_true", help="Atualiza a aba com o resultado completo.")
    args = parser.parse_args(argv)

    if args.create_sheet:
        if os.getenv(PROFITABILITY_SHEET_KEY, "").strip():
            parser.error(f"{PROFITABILITY_SHEET_KEY} já aponta para uma planilha.")
        credential = authenticate_write()
        sheets = build("sheets", "v4", credentials=credential, cache_discovery=False)
        created = create_empty_sheet(sheets, workbook_title=WORKBOOK_TITLE, **SHEET_OPTIONS)
        print(f"{PROFITABILITY_SHEET_KEY}={created['spreadsheetId']}")
        print(created.get("spreadsheetUrl", WORKBOOK_TITLE))
        return

    spreadsheet_id = get_profitability_spreadsheet_id() if args.publish else None
    with bigquery.Client(project=GCP_PROJECT, location=BQ_LOCATION) as client:
        sync_profitability(client, spreadsheet_id=spreadsheet_id)


if __name__ == "__main__":
    main()
