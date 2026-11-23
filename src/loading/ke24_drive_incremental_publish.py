"""Publicacao incremental do processed KE24 com autorizacao explicita.

Nao apaga artefatos em falhas. O lock local nao substitui coordenacao distribuida.
"""

import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pandas as pd
from googleapiclient.http import MediaIoBaseUpload

from src.loading.ke24_drive_manifest import (
    MANIFEST_NAME,
    get_processed_folder_id,
    list_processed_files,
    load_processed_manifest,
    serialize_processed_manifest,
    validate_manifest_document,
)
from src.loading.ke24_drive_publish import _upload_and_verify
from src.loading.plan_ke24_incremental import BUSINESS_KEY, FILE_ID, MANIFEST_COLUMNS

LOCK_PATH = (
    Path.home()
    / "AppData"
    / "Local"
    / "KE24"
    / "oauth"
    / "locks"
    / "incremental_processed.lock"
)


@dataclass(frozen=True)
class ProcessedState:
    manifest_id: str
    manifest_sha256: str
    object_ids: frozenset[str]
    manifest: pd.DataFrame
    batches: tuple[dict, ...]


@contextmanager
def _local_publish_lock(path=None):
    """Evita duas publicacoes concorrentes no mesmo computador."""
    path = Path(LOCK_PATH if path is None else path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise RuntimeError(
            "Publicacao bloqueada: existe outra execucao ou um lock pendente. "
            "Verifique manualmente antes de remover o lock."
        ) from None
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def _assert_same_manifest(left, right):
    """Compara arquivos e metadados sem imprimir identificadores protegidos."""
    try:
        pd.testing.assert_frame_equal(
            left[MANIFEST_COLUMNS].sort_values(FILE_ID).reset_index(drop=True),
            right[MANIFEST_COLUMNS].sort_values(FILE_ID).reset_index(drop=True),
            check_dtype=False,
        )
    except (AssertionError, KeyError, ValueError) as exc:
        raise RuntimeError("Manifesto remoto mudou; refaca o planejamento.") from None


def _require_active_processed_folder(service):
    folder_id = get_processed_folder_id()
    metadata = service.files().get(
        fileId=folder_id, fields="id,mimeType,trashed"
    ).execute()
    if (
        metadata.get("id") != folder_id
        or metadata.get("mimeType") != "application/vnd.google-apps.folder"
        or metadata.get("trashed")
    ):
        raise RuntimeError("Pasta processed indisponivel ou na lixeira.")


def read_processed_state(service):
    """Recupera somente estado remoto validado, inclusive lotes anteriores."""
    _require_active_processed_folder(service)
    verified = load_processed_manifest(service)
    if verified.empty:
        raise RuntimeError("Publicacao incremental exige manifesto existente e valido.")
    items = list_processed_files(service)
    manifests = [item for item in items if item["name"] == MANIFEST_NAME]
    if len(manifests) != 1:
        raise RuntimeError("Manifesto ausente ou duplicado; reconciliacao necessaria.")
    manifest_id = manifests[0]["id"]
    payload = service.files().get_media(fileId=manifest_id).execute()
    document = json.loads(payload.decode("utf-8"))
    snapshot_manifest = validate_manifest_document(document)
    _assert_same_manifest(verified, snapshot_manifest)
    expected = {manifest_id} | {batch["parquet_id"] for batch in document["batches"]}
    if {item["id"] for item in items} != expected:
        raise RuntimeError("Artefatos remotos divergentes; reconciliacao necessaria.")
    return ProcessedState(
        manifest_id=manifest_id,
        manifest_sha256=hashlib.sha256(payload).hexdigest(),
        object_ids=frozenset(expected),
        manifest=snapshot_manifest,
        batches=tuple(document["batches"]),
    )


def build_incremental_plan(state, new_manifest, parquet_buffer, processed_row_count):
    """Valida um lote adicional preservando todas as referencias anteriores."""
    if not isinstance(state, ProcessedState) or state.manifest.empty:
        raise ValueError("Estado processed anterior obrigatorio.")
    if (
        not isinstance(new_manifest, pd.DataFrame)
        or new_manifest.empty
        or new_manifest.columns.duplicated().any()
        or set(new_manifest.columns) != set(MANIFEST_COLUMNS)
    ):
        raise ValueError("Manifesto de novas extracoes invalido.")
    if not isinstance(processed_row_count, int) or processed_row_count <= 0:
        raise ValueError("Quantidade de registros processados invalida.")
    if (
        new_manifest[FILE_ID].isna().any()
        or new_manifest[FILE_ID].duplicated().any()
        or new_manifest[BUSINESS_KEY].isna().any().any()
    ):
        raise ValueError("Identificadores de novos arquivos invalidos ou duplicados.")
    counts = pd.to_numeric(new_manifest["record_count"], errors="coerce")
    if counts.isna().any() or counts.le(0).any() or counts.mod(1).ne(0).any():
        raise ValueError("Contagens de origem invalidas.")
    if int(counts.sum()) != processed_row_count:
        raise ValueError("Total processed diverge do manifesto de origem.")

    combined = pd.concat(
        [state.manifest[MANIFEST_COLUMNS], new_manifest[MANIFEST_COLUMNS]],
        ignore_index=True,
    )
    data = parquet_buffer.getvalue()
    if not data:
        raise ValueError("Parquet incremental vazio.")
    source_ids = sorted(new_manifest[FILE_ID].astype(str).tolist())
    batch_id = hashlib.sha256("\n".join(source_ids).encode("utf-8")).hexdigest()[:16]
    batch = {
        "batch_id": batch_id,
        "parquet_id": "PENDING_INCREMENTAL_UPLOAD",
        "parquet_name": f"ke24_processed_{batch_id}.parquet",
        "parquet_sha256": hashlib.sha256(data).hexdigest(),
        "byte_count": len(data),
        "record_count": processed_row_count,
        "source_file_ids": source_ids,
    }
    # O validador existente bloqueia periodos/hashes/lotes repetidos e somas divergentes.
    serialize_processed_manifest(combined, [*state.batches, batch])
    return {"base": state, "manifest": combined, "batch": batch}


def preview_incremental_publication(
    read_service, loaded_manifest, new_manifest, parquet_buffer, processed_row_count
):
    state = read_processed_state(read_service)
    _assert_same_manifest(loaded_manifest, state.manifest)
    plan = build_incremental_plan(state, new_manifest, parquet_buffer, processed_row_count)
    print("\nPrevia incremental: nenhum upload realizado.")
    print(f"Lotes anteriores preservados: {len(state.batches)}")
    print(f"Novas extracoes planejadas: {len(new_manifest)}")
    print(f"Novos registros processed: {processed_row_count}")
    return plan


def _ensure_unchanged(service, state, additional_id=None):
    current = list_processed_files(service)
    current_ids = {item["id"] for item in current}
    expected_ids = set(state.object_ids)
    if additional_id is not None:
        expected_ids.add(additional_id)
    if current_ids != expected_ids:
        raise RuntimeError("Conteudo da pasta mudou; reconciliacao manual necessaria.")
    payload = service.files().get_media(fileId=state.manifest_id).execute()
    if hashlib.sha256(payload).hexdigest() != state.manifest_sha256:
        raise RuntimeError("Manifesto mudou durante a publicacao; reconciliacao necessaria.")


def _check_write_permissions(service, state):
    # A credencial drive.file pode nao ter acesso a um arquivo movido de pasta.
    visible_ids = {item["id"] for item in list_processed_files(service)}
    if visible_ids != set(state.object_ids):
        raise RuntimeError("Token de escrita nao enxerga todos os artefatos antigos.")
    metadata = service.files().get(
        fileId=state.manifest_id,
        fields="id,trashed,capabilities(canEdit)",
    ).execute()
    if (
        metadata.get("id") != state.manifest_id
        or metadata.get("trashed")
        or not metadata.get("capabilities", {}).get("canEdit")
    ):
        raise RuntimeError("Token de escrita nao pode editar o manifesto existente.")


def publish_incremental_batch(read_service, write_service, plan, parquet_buffer):
    """Publica novo Parquet e ATUALIZA o mesmo manifesto, nunca cria outro JSON.

    Se o processo falhar apos iniciar upload, NAO repetir: inspecionar Drive.
    """
    with _local_publish_lock():
        base = plan["base"]
        if not isinstance(base, ProcessedState):
            raise ValueError("Plano incremental sem estado remoto validado.")
        # Read-after-plan, evitando escrever com um manifesto obsoleto.
        current = read_processed_state(read_service)
        if current.manifest_id != base.manifest_id or current.manifest_sha256 != base.manifest_sha256:
            raise RuntimeError("Estado remoto mudou desde a previa; refaca o planejamento.")
        _check_write_permissions(write_service, current)
        _ensure_unchanged(read_service, current)

        batch = plan["batch"]
        payload = parquet_buffer.getvalue()
        if len(payload) != batch["byte_count"] or hashlib.sha256(payload).hexdigest() != batch["parquet_sha256"]:
            raise RuntimeError("Parquet mudou apos a previa; publicacao bloqueada.")
        if plan["manifest"].empty:
            raise ValueError("Manifesto planejado invalido.")
        # Verifica o plano ANTES de abrir a janela de escrita.
        serialize_processed_manifest(plan["manifest"], [*current.batches, batch])

        try:
            # Se houver falha a partir deste ponto, o estado pode ser parcial.
            parquet_id = _upload_and_verify(
                write_service,
                get_processed_folder_id(),
                batch["parquet_name"],
                payload,
                "application/octet-stream",
            )
            _ensure_unchanged(read_service, current, additional_id=parquet_id)
            committed_batch = {**batch, "parquet_id": parquet_id}
            new_manifest_bytes = serialize_processed_manifest(
                plan["manifest"], [*current.batches, committed_batch]
            )
            media = MediaIoBaseUpload(
                BytesIO(new_manifest_bytes), mimetype="application/json", resumable=False
            )
            result = write_service.files().update(
                fileId=current.manifest_id,
                media_body=media,
                fields="id,name",
            ).execute()
            if result.get("id") != current.manifest_id or result.get("name") != MANIFEST_NAME:
                raise RuntimeError("Atualizacao do manifesto sem confirmacao consistente.")
            received = read_service.files().get_media(fileId=current.manifest_id).execute()
            if hashlib.sha256(received).digest() != hashlib.sha256(new_manifest_bytes).digest():
                raise RuntimeError("Manifesto recuperado diverge da atualizacao.")
            verified = load_processed_manifest(read_service)
            _assert_same_manifest(plan["manifest"], verified)
        except Exception:
            raise RuntimeError(
                "Publicacao parcial ou indeterminada: NAO execute novamente. "
                "Confira Parquet e manifesto no Drive e reconcilie manualmente."
            ) from None

        print("Publicacao incremental confirmada; manifesto anterior atualizado no mesmo arquivo.")
        print(f"Extracoes totais registradas: {len(verified)}")
        return {"processed_files": len(verified), "new_files": len(batch["source_file_ids"])}
