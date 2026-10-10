import re
import uuid

from google.api_core.exceptions import (
    NotFound,
    PreconditionFailed,
)

LOCK_OBJECT_NAME = "locks/KE24_staging_protected.lock"

RUN_ID_PATTERN = re.compile(r"[0-9a-f]{32}")

class GCSExecutionLock:

    #Controla a exclusividade de uma eecução por meio de um objeto no Cloud Storage
    #O cliente GCS é recebido de fora pra permitir testes sem conexão com a nuvem
    def __init__(
        self,
        storage_client,
        bucket_name,
        run_id=None,
    ):
        if (
            not isinstance(bucket_name, str)
            or not bucket_name.strip()
        ):
            raise ValueError(
                "Bucket de controle não configurado."
            )

        self.run_id = run_id or uuid.uuid4().hex

        if not RUN_ID_PATTERN.fullmatch(self.run_id):
            raise ValueError(
                "Identificador de execução inválido."
            )

        self.blob = (
            storage_client
            .bucket(bucket_name)
            .blob(LOCK_OBJECT_NAME)
        )

        self.generation = None

    @property
    def acquired(self):
        return self.generation is not None

    def acquire(self):
        #Criação o objeto da exclusividade, retorna True quando consegue adquirir e False quando o objeto já existe
        if self.acquired:
            raise RuntimeError(
                "Esta execução já possui um lock."
            )

        try:
            self.blob.upload_from_string(
                self.run_id,
                content_type="text/plain",
                if_generation_match=0,
            )

        except PreconditionFailed:
            return False

        generation = self.blob.generation

        if generation is None:
            raise RuntimeError(
                "Lock criado sem confirmação da geração. Reconciliação manual necessária."
            )

        generation = int(generation)

        if generation <= 0:
            raise RuntimeError(
                "Geração do lock inválida. Reconciliação manual necessária."
            )

        self.generation = generation

        return True

    def release(self):
        #Libera somente a geração adquiridda por esta instancia. Não será chamado automaticamente após falha ou resultado desconheido
        if not self.acquired:
            raise RuntimeError(
                "Esta execução não possui um lock."
            )

        try:
            self.blob.delete(
                if_generation_match=self.generation
            )

        except (PreconditionFailed, NotFound):
            raise RuntimeError(
                "Não foi possível confirmar a liberação do lock. Reconciliação manual necessária."
            ) from None

        self.generation = None
