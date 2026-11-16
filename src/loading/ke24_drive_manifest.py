import hashlib
import json
import os
import re

import pandas as pd

from src.loading.plan_ke24_incremental import (
    BUSINESS_KEY,
    FILE_ID,
    MANIFEST_COLUMNS,
)

MANIFEST_NAME = "ke24_ingestion_manifest.json"
MANIFEST_VERSION = 2
BATCH_FIELDS = {
    "batch_id",
    "parquet_id",
    "parquet_name",
    "parquet_sha256",
    "byte_count",
    "record_count",
    "source_file_ids",
}


def get_processed_folder_id():
    folder_id = os.getenv("KE24_DRIVE_PROCESSED_FOLDER_ID")
    if not folder_id or not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id):
        raise ValueError("KE24_DRIVE_PROCESSED_FOLDER_ID inválido ou ausente.")
    return folder_id


def list_processed_files(service):
    """Lista TODOS os itens da pasta, inclusive páginas adicionais."""
    folder_id = get_processed_folder_id()
    files = []
    page_token = None
    while True:
        page = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="nextPageToken,files(id,name,mimeType)",
            pageSize=100,
            pageToken=page_token,
        ).execute()
        files.extend(page.get("files", []))
        page_token = page.get("nextPageToken")
        if not page_token:
            return files


def validate_manifest_document(document):
    """Valida formato, unicidade, totais e vínculo extração -> lote."""
    if not isinstance(document, dict) or set(document) != {
        "schema_version", "files", "batches"
    }:
        raise ValueError("Estrutura do manifesto inválida.")
    if document["schema_version"] != MANIFEST_VERSION:
        raise ValueError("Versão do manifesto não suportada. Revisão necessária.")

    records = document["files"]
    batches = document["batches"]
    if not isinstance(records, list) or not isinstance(batches, list):
        raise ValueError("Listas de extrações ou lotes inválidas.")
    if not records or not batches:
        raise ValueError("Manifesto publicado não pode estar vazio.")

    if any(not isinstance(row, dict) or set(row) != set(MANIFEST_COLUMNS)
           for row in records):
        raise ValueError("Colunas de extração incompatíveis com o contrato.")

    manifest = pd.DataFrame(records, columns=MANIFEST_COLUMNS)
    if manifest.isna().any().any():
        raise ValueError("Manifesto contém valores nulos.")
    if manifest[FILE_ID].duplicated().any():
        raise ValueError("Manifesto contém extrações duplicadas.")
    if manifest.duplicated(BUSINESS_KEY).any():
        raise ValueError("Manifesto contém períodos de negócio duplicados.")

    counts = pd.to_numeric(manifest["record_count"], errors="coerce")
    if counts.isna().any() or counts.le(0).any() or counts.mod(1).ne(0).any():
        raise ValueError("Manifesto possui quantidade de registros inválida.")
    manifest["record_count"] = counts.astype("int64")
    if not manifest[FILE_ID].astype(str).str.fullmatch(r"TKN_[0-9a-f]{32}").all():
        raise ValueError("Identificadores de origem não estão protegidos.")

    referenced = []
    batch_ids = set()
    parquet_ids = set()
    parquet_names = set()
    counts_by_source = dict(zip(manifest[FILE_ID], manifest["record_count"]))

    for batch in batches:
        if not isinstance(batch, dict) or set(batch) != BATCH_FIELDS:
            raise ValueError("Metadados de lote incompatíveis.")
        batch_id = batch["batch_id"]
        parquet_name = batch["parquet_name"]
        if not isinstance(batch_id, str) or not re.fullmatch(r"[0-9a-f]{16}", batch_id):
            raise ValueError("Identificador de lote inválido.")
        if parquet_name != f"ke24_processed_{batch_id}.parquet":
            raise ValueError("Nome de Parquet incompatível com lote.")
        if not isinstance(batch["parquet_id"], str) or not batch["parquet_id"]:
            raise ValueError("ID remoto do Parquet inválido.")
        if not isinstance(batch["parquet_sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", batch["parquet_sha256"]
        ):
            raise ValueError("Hash do Parquet inválido.")
        if (not isinstance(batch["record_count"], int)
                or isinstance(batch["record_count"], bool)
                or batch["record_count"] <= 0
                or not isinstance(batch["byte_count"], int)
                or isinstance(batch["byte_count"], bool)
                or batch["byte_count"] <= 0):
            raise ValueError("Contagens do lote inválidas.")
        ids = batch["source_file_ids"]
        if not isinstance(ids, list) or not ids or not all(
            isinstance(item, str) and item in counts_by_source for item in ids
        ):
            raise ValueError("Referências de extrações inválidas no lote.")
        if len(set(ids)) != len(ids):
            raise ValueError("Extração repetida dentro de um lote.")
        if sum(int(counts_by_source[item]) for item in ids) != batch["record_count"]:
            raise ValueError("Total de registros incompatível com as extrações.")
        if batch_id in batch_ids or batch["parquet_id"] in parquet_ids or parquet_name in parquet_names:
            raise ValueError("Lotes ou arquivos Parquet duplicados.")
        batch_ids.add(batch_id)
        parquet_ids.add(batch["parquet_id"])
        parquet_names.add(parquet_name)
        referenced.extend(ids)

    if len(referenced) != len(counts_by_source) or set(referenced) != set(counts_by_source):
        raise ValueError("Há extrações sem Parquet correspondente ou vinculadas duas vezes.")

    return manifest


def serialize_processed_manifest(manifest, batches):
    """Serializa apenas um manifesto com lotes publicados e confirmados."""
    if manifest.columns.duplicated().any() or set(manifest.columns) != set(MANIFEST_COLUMNS):
        raise ValueError("Colunas do manifesto inválidas.")
    records = json.loads(manifest[MANIFEST_COLUMNS].to_json(orient="records"))
    document = {
        "schema_version": MANIFEST_VERSION,
        "files": records,
        "batches": batches,
    }
    validate_manifest_document(document)
    return json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")


def load_processed_manifest(service):
    """Retorna apenas extrações COMMITADAS e com arquivos verificados."""
    files = list_processed_files(service)
    manifests = [file for file in files if file["name"] == MANIFEST_NAME]
    if len(manifests) > 1:
        raise RuntimeError("Manifestos duplicados: reconciliação necessária.")
    if not manifests:
        if files:
            raise RuntimeError(
                "Arquivos no Drive sem manifesto. Publicação incompleta: "
                "reconciliação necessária."
            )
        print("Pasta processed vazia. Primeira carga identificada.")
        return pd.DataFrame(columns=MANIFEST_COLUMNS)

    content = service.files().get_media(fileId=manifests[0]["id"]).execute()
    document = json.loads(content.decode("utf-8"))
    manifest = validate_manifest_document(document)

    # Não aceitar artefatos órfãos, ausentes ou com nomes divergentes.
    expected_ids = {manifests[0]["id"]}
    expected_ids.update(batch["parquet_id"] for batch in document["batches"])
    if {file["id"] for file in files} != expected_ids:
        raise RuntimeError("Arquivos ausentes ou não registrados: reconciliar a pasta.")

    by_id = {file["id"]: file for file in files}
    for batch in document["batches"]:
        remote = by_id[batch["parquet_id"]]
        if remote["name"] != batch["parquet_name"]:
            raise RuntimeError("Nome de Parquet divergente do manifesto.")

        # Verificação forte. Para lotes grandes, substituir por leitura em chunks.
        payload = service.files().get_media(fileId=batch["parquet_id"]).execute()
        if len(payload) != batch["byte_count"] or hashlib.sha256(payload).hexdigest() != batch["parquet_sha256"]:
            raise RuntimeError("Integridade do Parquet comprometida: reconciliação necessária.")

    print(f"Manifesto verificado: {len(manifest)} extrações protegidas.")
    return manifest
