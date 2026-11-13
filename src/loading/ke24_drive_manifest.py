
import json
import os
import re

import pandas as pd

from src.loading.plan_ke24_incremental import MANIFEST_COLUMNS


MANIFEST_NAME = "ke24_ingestion_manifest.json"


def load_processed_manifest(service):

    folder_id = os.getenv("KE24_DRIVE_PROCESSED_FOLDER_ID")

    if not folder_id or not re.fullmatch(
        r"[A-Za-z0-9_-]+", folder_id
    ):
        raise ValueError(
            "KE24_DRIVE_PROCESSED_FOLDER_ID inválido ou não configurado."
        )

    files = []
    page_token = None

    # Consulta os arquivos existentes na pasta processed.
    while True:
        response = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType)",
            pageSize=100,
            pageToken=page_token,
        ).execute()

        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")

        if not page_token:
            break

    manifests = [
        file
        for file in files
        if file["name"] == MANIFEST_NAME
    ]

    if len(manifests) > 1:
        raise RuntimeError(
            "Existem múltiplos manifestos na pasta processed."
        )

    # Primeira execução: não existe manifesto nem Parquet.
    if not manifests:
        if files:
            raise RuntimeError(
                "A pasta processed contém arquivos, "
                "mas não possui um manifesto de controle. "
                "É necessária uma reconciliação."
            )

        print(
            "Pasta processed vazia. "
            "Primeira carga identificada."
        )

        return pd.DataFrame(columns=MANIFEST_COLUMNS)

    # Esta verificação só ocorre se já existe um manifesto.
    parquet_files = [
        file
        for file in files
        if (
            file["name"].startswith("ke24_processed_")
            and file["name"].endswith(".parquet")
        )
    ]

    if not parquet_files:
        raise RuntimeError(
            "Manifesto encontrado sem Parquet correspondente. "
            "Reconciliação necessária."
        )

    # A partir daqui, continua o código existente
    # que lê e valida o manifesto.
    content = service.files().get_media(
        fileId=manifests[0]["id"]
    ).execute()

    document = json.loads(content.decode("utf-8"))

    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or not isinstance(document.get("files"), list)
    ):
        raise ValueError("Formato do manifesto inválido.")

    records = document["files"]
    expected = set(MANIFEST_COLUMNS)

    if any(
        not isinstance(record, dict)
        or set(record) != expected
        for record in records
    ):
        raise ValueError(
            "O manifesto possui campos incompatíveis."
        )

    manifest = pd.DataFrame(
        records,
        columns=MANIFEST_COLUMNS,
    )

    print(
        f"Manifesto carregado: {len(manifest)} extrações."
    )

    return manifest


def serialize_processed_manifest(manifest):
    #Converte o manifesto validado em JSON para persistência

    if manifest.columns.duplicated().any():
        raise ValueError("Manifesto com colunas duplicadas.")

    if set(manifest.columns) != set(MANIFEST_COLUMNS):
        raise ValueError("Estrutura do manifesto inválida.")

    if manifest[MANIFEST_COLUMNS].isna().any().any():
        raise ValueError("Manifesto contém valores nulos.")

    if manifest["_source_file_sha256"].duplicated().any():
        raise ValueError("Manifesto contém arquivos duplicados.")

    records = json.loads(
        manifest[MANIFEST_COLUMNS].to_json(orient="records")
    )

    document = {
        "schema_version": 1,
        "files": records,
    }

    return json.dumps(
        document,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")

