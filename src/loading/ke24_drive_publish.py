import hashlib
import re
from io import BytesIO

from googleapiclient.http import MediaIoBaseUpload

from src.loading.ke24_drive_manifest import (
    MANIFEST_NAME,
    get_processed_folder_id,
    list_processed_files,
    serialize_processed_manifest,
)
from src.loading.plan_ke24_incremental import FILE_ID


def preview_first_publication(service, new_manifest, parquet_buffer):
    """Prévia SEM gravação para pasta que ainda não possui qualquer arquivo."""
    if new_manifest.empty or new_manifest[FILE_ID].duplicated().any():
        raise ValueError("Manifesto das novas extrações inválido.")
    if list_processed_files(service):
        raise RuntimeError("Pasta processed não vazia: primeira publicação bloqueada.")

    payload = parquet_buffer.getvalue()
    if not payload:
        raise ValueError("Parquet vazio.")
    source_ids = sorted(new_manifest[FILE_ID].astype(str).tolist())
    batch_id = hashlib.sha256("\n".join(source_ids).encode("utf-8")).hexdigest()[:16]
    parquet_name = f"ke24_processed_{batch_id}.parquet"
    parquet_sha256 = hashlib.sha256(payload).hexdigest()
    plan = {
        "batch_id": batch_id,
        "parquet_name": parquet_name,
        "parquet_sha256": parquet_sha256,
        "byte_count": len(payload),
        "source_file_ids": source_ids,
    }

    print("\nPrévia da primeira publicação:")
    print("Pasta de destino: vazia")
    print(f"Parquet planejado: {parquet_name}")
    print(f"Extrações: {len(source_ids)}")
    print(f"Tamanho: {len(payload)} bytes")
    print(f"SHA-256: {parquet_sha256}")
    print(f"Manifesto planejado: {MANIFEST_NAME} (schema 2)")
    print("Status: PRONTO PARA PUBLICAÇÃO")
    print("Nenhum upload realizado.")
    return plan


def _upload_and_verify(service, folder_id, name, content, mime_type):
    media = MediaIoBaseUpload(
        BytesIO(content), mimetype=mime_type, resumable=False
    )
    created = service.files().create(
        body={"name": name, "parents": [folder_id], "mimeType": mime_type},
        media_body=media,
        fields="id,name,parents",
    ).execute()

    if (not created.get("id") or created.get("name") != name
            or folder_id not in created.get("parents", [])):
        raise RuntimeError("Upload retornou metadados incompatíveis.")
    received = service.files().get_media(fileId=created["id"]).execute()
    if hashlib.sha256(received).digest() != hashlib.sha256(content).digest() or len(received) != len(content):
        raise RuntimeError("O conteúdo recuperado difere do upload.")
    return created["id"]


def publish_initial_batch(
    read_service,
    write_service,
    planned_batch,
    parquet_buffer,
    updated_manifest,
    processed_row_count,
):
    """Publica Parquet, valida-o, então publica o manifesto v2 com ID real.

    NÃO faz limpeza automática após falhas (respostas de API podem ser perdidas).
    """
    folder_id = get_processed_folder_id()
    if list_processed_files(read_service) or list_processed_files(write_service):
        raise RuntimeError("Destino não vazio; primeira publicação bloqueada.")
    if not isinstance(processed_row_count, int) or processed_row_count <= 0:
        raise ValueError("Quantidade processada inválida.")

    payload = parquet_buffer.getvalue()
    if len(payload) != planned_batch["byte_count"] or hashlib.sha256(payload).hexdigest() != planned_batch["parquet_sha256"]:
        raise RuntimeError("Parquet modificado depois da prévia; publicação bloqueada.")
    if not re.fullmatch(r"ke24_processed_[0-9a-f]{16}\.parquet", planned_batch["parquet_name"]):
        raise ValueError("Nome do Parquet inválido.")
    if int(updated_manifest["record_count"].sum()) != processed_row_count:
        raise ValueError("Total de registros do manifesto não corresponde ao Parquet.")
    if set(updated_manifest[FILE_ID]) != set(planned_batch["source_file_ids"]):
        raise ValueError("Extrações do manifesto não correspondem ao lote.")

    try:
        print("\nPublicando Parquet protegido...")
        parquet_id = _upload_and_verify(
            write_service, folder_id, planned_batch["parquet_name"],
            payload, "application/octet-stream",
        )
        print("Parquet enviado e verificado.")

        batch = {
            **planned_batch,
            "parquet_id": parquet_id,
            "record_count": processed_row_count,
        }
        manifest_bytes = serialize_processed_manifest(updated_manifest, [batch])

        print("Publicando manifesto vinculado ao Parquet...")
        manifest_id = _upload_and_verify(
            write_service, folder_id, MANIFEST_NAME,
            manifest_bytes, "application/json",
        )
        print("Manifesto enviado e verificado.")

    except Exception as exc:
        raise RuntimeError(
            "Publicação incompleta ou indeterminada: NÃO repita automaticamente. "
            "Confira a pasta processed e reconcilie os artefatos antes de prosseguir."
        ) from exc

    return {"parquet_id": parquet_id, "manifest_id": manifest_id}
