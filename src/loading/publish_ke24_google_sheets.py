
import hashlib
import hmac
import os
import sys
from pathlib import Path

import pandas as pd
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from src.privacy.hmac_key import load_hmac_key_from_environment


ROOT = Path(__file__).resolve().parents[2]

INPUT = (
    ROOT / "data" / "curated"
    / "ke24_temporal_signals_preview.csv"
)

TAB = "ke24_signals"

HEADERS = [
    "signal_id",
    "company_code",
    "currency",
    "period_key",
    "anomaly_score",
    "training_p95_threshold",
    "stability_rate",
    "new_measure_drift",
    "analysis_status",
]

REQUIRED = {
    "_source_file_sha256", "_source_row_number",
    "company_code", "currency", "period_key",
    "anomaly_score", "training_p95_threshold",
    "stability_rate", "new_measure_drift", "analysis_status",
}


def prepare_data():
    df = pd.read_csv(
        INPUT, encoding="utf-8-sig", dtype="string"
    )

    if df.empty or not REQUIRED.issubset(df.columns):
        raise ValueError("CSV vazio ou com colunas ausentes.")

    if df[list(REQUIRED)].isna().any().any():
        raise ValueError("Existem valores obrigatorios nulos.")

    identity = [
        "_source_file_sha256",
        "_source_row_number",
        "company_code",
    ]

    if df.duplicated(identity).any():
        raise ValueError("Identidades duplicadas.")

    row_numbers = df["_source_row_number"].astype("int64")

    if row_numbers.le(0).any():
        raise ValueError("Numeros de origem invalidos.")

    drift = df["new_measure_drift"].str.lower()

    if not drift.isin(["true", "false"]).all():
        raise ValueError("Indicador de drift invalido.")

    expected_status = drift.map({
        "true": "PENDING_DRIFT_REVIEW",
        "false": "PENDING_BUSINESS_CLASSIFICATION",
    })

    if not df["analysis_status"].eq(expected_status).all():
        raise ValueError("Status inconsistente.")

    scores = df["anomaly_score"].astype(float)
    thresholds = df["training_p95_threshold"].astype(float)
    stability = df["stability_rate"].astype(float)

    if not (
        scores.ge(thresholds).all()
        and stability.between(0, 100).all()
    ):
        raise ValueError("Scores ou estabilidade invalidos.")

    # Cria IDs estaveis sem publicar metadados de origem
    secret_key = load_hmac_key_from_environment()

    rows = [HEADERS]

    for i, record in df.iterrows():
        source_identity = "|".join([
            "KE24_SHEETS_SIGNAL_V1",
            record["_source_file_sha256"],
            record["_source_row_number"],
            record["company_code"],
        ])

        signal_id = "SIG_" + hmac.new(
            secret_key,
            source_identity.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:24]

        rows.append([
            signal_id,
            record["company_code"],
            record["currency"],
            record["period_key"],
            float(scores.loc[i]),
            float(thresholds.loc[i]),
            float(stability.loc[i]),
            drift.loc[i] == "true",
            record["analysis_status"],
        ])

    if len({row[0] for row in rows[1:]}) != len(rows) - 1:
        raise ValueError("IDs de sinal duplicados.")

    return rows



def connect():
    client_file = Path(
        os.environ["KE24_OAUTH_CLIENT_FILE"]
    )

    token_file = Path(
        os.environ["KE24_OAUTH_TOKEN_FILE"]
    )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets"
    ]

    if not client_file.is_file():
        raise FileNotFoundError(
            "Arquivo OAuth nao encontrado."
        )

    credentials = None

    # Reutiliza o token salvo anteriormente
    if token_file.exists():
        credentials = Credentials.from_authorized_user_file(
            str(token_file),
            scopes,
        )

    if not credentials or not credentials.valid:

        if (
            credentials
            and credentials.expired
            and credentials.refresh_token
        ):
            credentials.refresh(Request())

        else:
            # Abre o navegador na primeira autenticacao
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_file),
                scopes,
            )

            credentials = flow.run_local_server(
                port=0
            )

        token_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        token_file.write_text(
            credentials.to_json(),
            encoding="utf-8",
        )

    service = build(
        "sheets",
        "v4",
        credentials=credentials,
        cache_discovery=False,
    )

    spreadsheet_id = os.environ["KE24_SHEET_ID"]

    metadata = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields=(
            "sheets(properties("
            "sheetId,title,gridProperties(rowCount,columnCount)"
            "))"
        ),
    ).execute()

    sheet = next(
        (
            item["properties"]
            for item in metadata.get("sheets", [])
            if item["properties"]["title"] == TAB
        ),
        None,
    )

    if sheet is None:
        raise ValueError(
            f"A aba '{TAB}' nao existe na planilha."
        )

    return service, spreadsheet_id, sheet


def publish():
    rows = prepare_data()

    service, spreadsheet_id, sheet = connect()

    print("Autenticacao e acesso a planilha: OK")
    print("Sinais preparados:", len(rows) - 1)

    if sys.argv[1:] == ["--check"]:
        print("Verificacao concluida sem publicar dados.")
        return

    if sys.argv[1:] != ["--publish"]:
        raise SystemExit("Uso: --check ou --publish")

    sheet_id = sheet["sheetId"]
    grid = sheet["gridProperties"]

    height = grid["rowCount"]
    width = grid["columnCount"]

    requests = []

    if len(rows) > height:
        requests.append({
            "appendDimension": {
                "sheetId": sheet_id,
                "dimension": "ROWS",
                "length": len(rows) - height,
            }
        })

    if len(HEADERS) > width:
        requests.append({
            "appendDimension": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "length": len(HEADERS) - width,
            }
        })

    def cell(value):
        if isinstance(value, bool):
            return {"userEnteredValue": {"boolValue": value}}

        if isinstance(value, (int, float)):
            return {"userEnteredValue": {"numberValue": value}}

        return {"userEnteredValue": {"stringValue": str(value)}}

    requests.append({
        "updateCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": max(height, len(rows)),
                "startColumnIndex": 0,
                "endColumnIndex": len(HEADERS),
            },
            "rows": [
                {"values": [cell(value) for value in row]}
                for row in rows
            ],
            "fields": "userEnteredValue",
        }
    })

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()

    print("Publicacao concluida!")
    print("Sinais publicados:", len(rows) - 1)


if __name__ == "__main__":
    publish()



