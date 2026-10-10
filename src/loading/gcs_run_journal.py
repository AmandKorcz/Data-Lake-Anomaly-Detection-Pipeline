import json
from datetime import datetime, timezone

from google.api_core.exceptions import (
    NotFound,
    PreconditionFailed,
)

from src.loading.gcs_execution_lock import RUN_ID_PATTERN

JOURNAL_PREFIX = "runs/KE24_staging_protected"

REQUIRED_FIELDS = {
    "run_id",
    "state",
    "lock_generation",
    "created_at",
    "updated_at",
}

def current_timestamp():
    return datetime.now(timezone.utc).isoformat()

class GCSRunJournal:
    def __init__(self, storage_client, bucket_name, run_id):
        if not isinstance(bucket_name, str) or not bucket_name.strip():
            raise ValueError("Bucket não configurado.")

        if (
            not isinstance(run_id, str)
            or not RUN_ID_PATTERN.fullmatch(run_id)
        ):
            raise ValueError("Run ID inválido.")

        self.run_id = run_id
        self.object_name = f"{JOURNAL_PREFIX}/{run_id}.json"

        self.blob = (
            storage_client
            .bucket(bucket_name)
            .blob(self.object_name)
        )

    def start(self, execution_lock):
        #Registra o início somente após confirmação da exclusividade da mesma execução.

        if (
            execution_lock.run_id != self.run_id
            or execution_lock.acquired is not True
            or not isinstance(execution_lock.generation, int)
            or execution_lock.generation <= 0
        ):
            raise RuntimeError(
                "Não é possível registrar uma execução sem exclusividade confirmada."
            )

        timestamp = current_timestamp()

        record = {
            "run_id": self.run_id,
            "state": "RUNNING",
            "lock_generation": execution_lock.generation,
            "created_at": timestamp,
            "updated_at": timestamp,
        }

        try:
            self.blob.upload_from_string(
                json.dumps(record, sort_keys=True),
                content_type="application/json",
                if_generation_match=0,
            )
        except PreconditionFailed:
            raise RuntimeError(
                "Já existe um registro para esta execução. Reconciliação necessária."
            ) from None

        if not self.blob.generation:
            raise RuntimeError(
                "Criação do registro não confirmada. Reconciliação necessária."
            )

        return record

    def read(self):
        #Lê o registro e sua geração atual. A leitura de conteúdo exige a mesma geração observada na consulta de metadados.

        try:
            self.blob.reload()
        except NotFound:
            raise FileNotFoundError(
                "Registro da execução não encontrado."
            ) from None

        generation = self.blob.generation

        if generation is None or int(generation) <= 0:
            raise RuntimeError(
                "Geração do registro inválida."
            )

        generation = int(generation)

        try:
            content = self.blob.download_as_text(
                if_generation_match=generation,
            )
        except (NotFound, PreconditionFailed):
            raise RuntimeError(
                "O registro mudou durante a leitura. Reconciliação necessária."
            ) from None

        try:
            record = json.loads(content)
        except (ValueError, TypeError):
            raise ValueError(
                "Conteúdo do registro inválido."
            ) from None

        if (
            not isinstance(record, dict)
            or set(record) != REQUIRED_FIELDS
            or record["run_id"] != self.run_id
            or record["state"] not in {"RUNNING", "UNKNOWN"}
            or not isinstance(record["lock_generation"], int)
            or record["lock_generation"] <= 0
            or not isinstance(record["created_at"], str)
            or not isinstance(record["updated_at"], str)
        ):
            raise ValueError(
                "Registro incompatível com o contrato."
            )

        return record, generation

    def mark_unknown(self):
        #Registra que o resultado não está confirmado. Não libera locks nem autoriza uma nova carga

        record, generation = self.read()

        if record["state"] != "RUNNING":
            raise RuntimeError(
                "A execução já exige reconciliação."
            )

        record["state"] = "UNKNOWN"
        record["updated_at"] = current_timestamp()

        try:
            self.blob.upload_from_string(
                json.dumps(record, sort_keys=True),
                content_type="application/json",
                if_generation_match=generation,
            )
        except PreconditionFailed:
            raise RuntimeError(
                "O registro foi alterado por outra execução. Reconciliação necessária."
            ) from None

        if not self.blob.generation:
            raise RuntimeError(
                "Atualização não confirmada. Reconciliação necessária."
            )

        return record
