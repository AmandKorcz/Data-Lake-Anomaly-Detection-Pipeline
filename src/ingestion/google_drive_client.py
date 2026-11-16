from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import os

from io import BytesIO

import pandas as pd
from googleapiclient.http import MediaIoBaseDownload

OAUTH_DIR = Path.home() / "AppData/Local/KE24/oauth"

CLIENT_FILE = OAUTH_DIR / "oauth-client.json"
TOKEN_FILE = OAUTH_DIR / "drive-token.json"

WRITE_TOKEN_FILE = OAUTH_DIR / "drive-write-token.json"

WRITE_SCOPES = [
    "https://www.googleapis.com/auth/drive.file"
]

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly"
]


def authenticate():
    credentials = None

    if TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(
            str(TOKEN_FILE),
            SCOPES
        )

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CLIENT_FILE),
            SCOPES
        )

        credentials = flow.run_local_server(port=0)

    TOKEN_FILE.write_text(
        credentials.to_json(),
        encoding="utf-8"
    )

    return credentials

def read_xlsx_from_drive(service, file_id: str) -> BytesIO:

    buffer = BytesIO()

    request = service.files().get_media(
        fileId=file_id
    )

    downloader = MediaIoBaseDownload(
        buffer,
        request,
        chunksize=1024 * 1024
    )

    done = False

    while not done:
        _, done = downloader.next_chunk()

    buffer.seek(0)

    return buffer


def list_raw_xlsx_files(service):
    """Lista os arquivos XLSX da pasta raw do Google Drive."""

    folder_id = os.getenv("KE24_DRIVE_RAW_FOLDER_ID")

    if not folder_id:
        raise ValueError(
            "KE24_DRIVE_RAW_FOLDER_ID não configurado."
        )

    query = (
        f"'{folder_id}' in parents "
        "and trashed = false "
        "and mimeType = "
        "'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'"
    )

    files = []
    page_token = None

    while True:
        response = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            pageSize=100,
            pageToken=page_token
        ).execute()

        files.extend(
            file
            for file in response.get("files", [])
            if not file["name"].startswith("~$")
        )

        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return sorted(files, key=lambda file: file["name"])



def authenticate_write():
    #Autentica o Google Drive com permissão de escrita

    credentials = None

    if WRITE_TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(
            str(WRITE_TOKEN_FILE)
        )

        if not credentials.has_scopes(WRITE_SCOPES):
            raise ValueError(
                "O token de escrita não possui o escopo esperado."
            )

    if (
        credentials
        and credentials.expired
        and credentials.refresh_token
    ):
        credentials.refresh(Request())

    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CLIENT_FILE),
            WRITE_SCOPES,
        )

        credentials = flow.run_local_server(
            port=0,
            prompt="consent",
            access_type="offline",
        )

    if not credentials.has_scopes(WRITE_SCOPES):
        raise ValueError(
            "A autorização de escrita não foi concedida."
        )

    WRITE_TOKEN_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    WRITE_TOKEN_FILE.write_text(
        credentials.to_json(),
        encoding="utf-8",
    )

    return credentials

def main():
    credentials = authenticate()

    service = build(
        "drive",
        "v3",
        credentials=credentials
    )

    files = list_raw_xlsx_files(service)

    print("Conexão com Google Drive realizada!")


    print("\nValidando leitura dos arquivos XLSX...")

    for index, file in enumerate(files, start=1):
        if file["name"].startswith("~$"):
            continue

        with read_xlsx_from_drive(
            service,
            file["id"]
        ) as excel_buffer:

            # Lê somente os cabeçalhos do Excel.
            header_df = pd.read_excel(
                excel_buffer,
                nrows=0
            )

            print(
                f"Arquivo {index}/{len(files)}: "
                f"{len(header_df.columns)} colunas identificadas."
            )

    print("\nLeitura em memória concluída!")

if __name__ == "__main__":
    main()
