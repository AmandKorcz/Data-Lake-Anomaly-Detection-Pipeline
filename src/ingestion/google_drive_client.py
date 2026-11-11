from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


OAUTH_DIR = Path.home() / "AppData/Local/KE24/oauth"

CLIENT_FILE = OAUTH_DIR / "oauth-client.json"
TOKEN_FILE = OAUTH_DIR / "drive-token.json"

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


def main():
    credentials = authenticate()

    service = build(
        "drive",
        "v3",
        credentials=credentials
    )

    response = service.files().list(
        q=(
            "trashed = false and "
            "mimeType = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'"
        ),
        fields="nextPageToken, files(id, name, mimeType)",
        pageSize=100
    ).execute()

    files = response.get("files", [])

    print("Conexão com Google Drive realizada!")

    if not files:
        print("Nenhum arquivo XLSX encontrado.")
        return

    print("\nArquivos XLSX encontrados:")

    for file in files:
        print(f"- {file['name']}")


if __name__ == "__main__":
    main()
