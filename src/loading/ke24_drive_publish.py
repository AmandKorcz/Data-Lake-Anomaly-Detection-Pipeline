
import hashlib
import json
import os
import re

from src.loading.plan_ke24_incremental import FILE_ID

from io import BytesIO
from googleapiclient.http import MediaIoBaseUpload

MANIFEST_NAME = "ke24_ingestion_manifest.json"


def preview_first_publication(
    service,
    new_manifest,
    parquet_buffer,
    manifest_bytes,
):

    folder_id = os.getenv("KE24_DRIVE_PROCESSED_FOLDER_ID")

    if not folder_id or not re.fullmatch(
        r"[A-Za-z0-9_-]+", folder_id
    ):
        raise ValueError("Pasta processed não configurada.")

    if new_manifest.empty:
        raise ValueError("Nenhuma extração nova para publicar.")

    if parquet_buffer.getbuffer().nbytes == 0:
        raise ValueError("O Parquet está vazio.")

    document = json.loads(manifest_bytes.decode("utf-8"))

    if (
        document.get("schema_version") != 1
        or len(document.get("files", [])) != len(new_manifest)
    ):
        raise ValueError("Manifesto inicial incompatível.")

    # Verifica todos os arquivos presentes no destino.
    files = []
    page_token = None

    while True:
        response = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="nextPageToken,files(id,name,mimeType)",
            pageSize=100,
            pageToken=page_token,
        ).execute()

        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")

        if not page_token:
            break

    # Esta operação é exclusiva da primeira carga.
    # Não tentamos sobrescrever arquivos existentes.
    if files:
        raise RuntimeError(
            "A pasta processed não está vazia. "
            "Publicação inicial bloqueada para reconciliação."
        )

    # Identificador estável baseado nas extrações protegidas.
    source_ids = sorted(
        new_manifest[FILE_ID].astype(str).tolist()
    )

    batch_id = hashlib.sha256(
        "\n".join(source_ids).encode("utf-8")
    ).hexdigest()[:16]

    parquet_name = f"ke24_processed_{batch_id}.parquet"

    parquet_buffer.seek(0)

    parquet_hash = hashlib.sha256(
        parquet_buffer.getvalue()
    ).hexdigest()

    print("\nPrévia da primeira publicação:")
    print("Pasta de destino: OK")
    print("Arquivos existentes: 0")
    print(f"Parquet planejado: {parquet_name}")
    print(f"Extrações no manifesto: {len(new_manifest)}")
    print(f"Tamanho do Parquet: {parquet_buffer.getbuffer().nbytes} bytes")
    print(f"SHA-256 do Parquet: {parquet_hash}")
    print(f"Manifesto planejado: {MANIFEST_NAME}")
    print("Status: PRONTO PARA PUBLICAÇÃO")
    print("Nenhum upload realizado.")

    return parquet_name


def publish_initial_batch(
    read_service,
    write_service,
    parquet_name,
    parquet_buffer,
    manifest_bytes,
):
    """Publica a primeira carga e verifica os uploads."""

    folder_id = os.getenv("KE24_DRIVE_PROCESSED_FOLDER_ID")

    if not folder_id or not re.fullmatch(
        r"[A-Za-z0-9_-]+", folder_id
    ):
        raise ValueError("Pasta processed inválida.")

    # Verifica novamente o destino antes de publicar.
    response = read_service.files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        fields="nextPageToken,files(id,name)",
        pageSize=1,
    ).execute()

    if response.get("files") or response.get("nextPageToken"):
        raise RuntimeError(
            "Pasta processed não está vazia. "
            "Publicação inicial bloqueada."
        )

    parquet_bytes = parquet_buffer.getvalue()

    if not parquet_bytes or not manifest_bytes:
        raise ValueError("Artefatos de publicação vazios.")

    def upload_and_verify(name, content, mime_type):
        media = MediaIoBaseUpload(
            BytesIO(content),
            mimetype=mime_type,
            resumable=False,
        )

        created = write_service.files().create(
            body={
                "name": name,
                "parents": [folder_id],
                "mimeType": mime_type,
            },
            media_body=media,
            fields="id,name,parents",
        ).execute()

        if (
            created.get("name") != name
            or folder_id not in created.get("parents", [])
        ):
            raise RuntimeError(
                "Arquivo publicado fora do destino esperado."
            )

        downloaded = write_service.files().get_media(
            fileId=created["id"]
        ).execute()

        if downloaded != content:
            raise RuntimeError(
                f"Falha na verificação do arquivo {name}."
            )

        return created

    try:
        print("\nPublicando Parquet protegido...")

        parquet_file = upload_and_verify(
            parquet_name,
            parquet_bytes,
            "application/octet-stream",
        )

        print("Parquet publicado e verificado!")

        # Somente depois do Parquet confirmado.
        print("\nPublicando manifesto...")

        manifest_file = upload_and_verify(
            MANIFEST_NAME,
            manifest_bytes,
            "application/json",
        )

        print("Manifesto publicado e verificado!")

    except Exception as error:
        raise RuntimeError(
            "Publicação incompleta ou indeterminada. "
            "Não repita o upload automaticamente. "
            "Confira os arquivos existentes no Drive "
            "e realize a reconciliação."
        ) from error

    return {
        "parquet_id": parquet_file["id"],
        "manifest_id": manifest_file["id"],
    }