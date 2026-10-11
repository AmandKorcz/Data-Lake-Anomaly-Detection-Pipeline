import hashlib
import hmac
import json

import pandas as pd
from google.api_core.exceptions import NotFound, PreconditionFailed

from src.loading.gcs_execution_lock import RUN_ID_PATTERN
from src.loading.gcs_run_journal import JOURNAL_PREFIX
from src.loading.ke24_batch_reconciliation import (
    _index_manifest,
    reconcile_observed_batch,
)
from src.loading.ke24_execution_control import ExecutionDecision
from src.loading.plan_ke24_incremental import (
    BUSINESS_KEY,
    FILE_ID,
    MANIFEST_COLUMNS,
)

FORMAT_VERSION = 1
EXPECTED_FIELDS = {"format_version", "run_id", "lock_generation", "files", "sha256"}


def _json_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")


def _normalize_rows(manifest):
    #Retorna apenas os seis metadados permitidos, ordenados por token
    try:
        by_file, _ = _index_manifest(manifest, allow_empty=False)
        rows = []
        for file_id, (month, count) in by_file.items():
            row = {FILE_ID: file_id}
            for name, value in zip(BUSINESS_KEY, month):
                row[name] = value.item() if hasattr(value, "item") else value
            row["record_count"] = int(count)
            rows.append(row)
        rows.sort(key=lambda item: item[FILE_ID])
        _json_bytes(rows)
        return rows
    except (ValueError, TypeError, OverflowError, KeyError):
        raise ValueError("Manifesto esperado inválido.") from None

def _journal_lock_generation(journal, run_id, *, for_write):
    if journal.run_id != run_id:
        raise ValueError("Diário e manifesto pertencem a execuções diferentes.")
    record, generation = journal.read()
    if (
        not isinstance(record, dict)
        or record.get("run_id") != run_id
        or record.get("state") not in ({"RUNNING"} if for_write else {"RUNNING", "UNKNOWN"})
        or type(record.get("lock_generation")) is not int
        or record["lock_generation"] <= 0
        or type(generation) is not int
        or generation <= 0
    ):
        raise RuntimeError("Diário não confirma a execução.")
    return record["lock_generation"]

class GCSExpectedBatchStore:
    #Armazena apenas o manifesto protegido de uma execução. Sem BigQuery

    def __init__(self, storage_client, bucket_name, run_id):
        if not isinstance(bucket_name, str) or not bucket_name.strip():
            raise ValueError("Bucket inválido.")
        if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("Identificador de execução inválido.")
        self.run_id = run_id
        self.object_name = f"{JOURNAL_PREFIX}/manifests/{run_id}.json"
        self.blob = storage_client.bucket(bucket_name).blob(self.object_name)

    def save(self, *, expected_manifest, run_journal, execution_lock):
        #Cria uma única vez; não sobrescreve e não libera o lock
        if (
            execution_lock.run_id != self.run_id
            or execution_lock.acquired is not True
            or type(execution_lock.generation) is not int
            or execution_lock.generation <= 0
        ):
            raise RuntimeError("Lock não confirmado para esta execução.")
        journal_lock_generation = _journal_lock_generation(
            run_journal, self.run_id, for_write=True,
        )
        if execution_lock.generation != journal_lock_generation:
            raise RuntimeError("Geração do lock e do diário divergentes.")

        rows = _normalize_rows(expected_manifest)
        body = {
            "format_version": FORMAT_VERSION,
            "run_id": self.run_id,
            "lock_generation": journal_lock_generation,
            "files": rows,
        }
        payload = {
            **body,
            "sha256": hashlib.sha256(_json_bytes(body)).hexdigest(),
        }
        try:
            self.blob.upload_from_string(
                _json_bytes(payload).decode("utf-8"),
                content_type="application/json",
                if_generation_match=0,
            )
        except PreconditionFailed:
            raise RuntimeError(
                "Manifesto já registrado; não sobrescrever. Reconciliação necessária."
            ) from None
        if type(self.blob.generation) not in (str, int):
            raise RuntimeError("Persistência do manifesto não confirmada.")
        try:
            if int(self.blob.generation) <= 0:
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            raise RuntimeError("Persistência do manifesto não confirmada.") from None
        recovered = self.read(run_journal=run_journal)
        if _normalize_rows(recovered) != rows:
            raise RuntimeError("O manifesto persistido não corresponde ao lote esperado.")
        return recovered

    def read(self, *, run_journal):
        #Recupera após reinício e confere a geração do diário
        lock_generation = _journal_lock_generation(
            run_journal, self.run_id, for_write=False,
        )
        try:
            self.blob.reload()
        except NotFound:
            raise FileNotFoundError("Manifesto esperado ausente.") from None

        try:
            generation = int(self.blob.generation)
            if generation <= 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise RuntimeError("Geração do manifesto inválida.") from None

        try:
            content = self.blob.download_as_text(
                if_generation_match=generation,
            )
        except (NotFound, PreconditionFailed):
            raise RuntimeError("Manifesto alterado durante a leitura.") from None

        try:
            payload = json.loads(content)
            if not isinstance(payload, dict) or set(payload) != EXPECTED_FIELDS:
                raise ValueError
            if (
                payload["format_version"] != FORMAT_VERSION
                or type(payload["format_version"]) is not int
                or payload["run_id"] != self.run_id
                or type(payload["lock_generation"]) is not int
                or payload["lock_generation"] != lock_generation
                or not isinstance(payload["files"], list)
                or not payload["files"]
                or not all(
                    isinstance(item, dict)
                    and set(item) == set(MANIFEST_COLUMNS)
                    for item in payload["files"]
                )
            ):
                raise ValueError
            body = {key: payload[key] for key in EXPECTED_FIELDS - {"sha256"}}
            expected_hash = hashlib.sha256(_json_bytes(body)).hexdigest()
            if (
                not isinstance(payload["sha256"], str)
                or not hmac.compare_digest(payload["sha256"], expected_hash)
            ):
                raise ValueError
            frame = pd.DataFrame(payload["files"], columns=MANIFEST_COLUMNS)
            if _normalize_rows(frame) != payload["files"]:
                raise ValueError
        except (TypeError, ValueError, OverflowError, KeyError):
            raise ValueError("Manifesto persistido inválido.") from None

        return frame

def reconcile_persisted_batch(
    *, batch_store, run_journal, storage_client,
    bucket_name, bigquery_client, remote_manifest,
):
    try:
        expected_manifest = batch_store.read(run_journal=run_journal)
    except Exception:
        return ExecutionDecision(
            action="RECONCILE",
            reason="Manifesto esperado indisponível ou inconsistente.",
        )
    return reconcile_observed_batch(
        run_journal=run_journal,
        storage_client=storage_client,
        bucket_name=bucket_name,
        bigquery_client=bigquery_client,
        expected_manifest=expected_manifest,
        remote_manifest=remote_manifest,
    )