from dataclasses import dataclass
import re

from src.loading.ke24_execution_control import (
    ExecutionDecision,
    MANIFEST_STATES,
)

RUN_ID_PATTERN = re.compile(r"[0-9a-f]{32}")

LOCK_STATES = {
    "PRESENT",
    "MISSING",
    "UNKNOWN",
}

JOB_STATES = {
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "UNKNOWN",
    "NOT_FOUND",
}

@dataclass(frozen=True)
class LockObservation:
    status: str
    owner_run_id: str | None = None
    generation: int | None = None


@dataclass(frozen=True)
class JobObservation:
    status: str
    job_id: str

def expected_job_id(run_id):
    #Define o identificador previsto para um job. Futuramente, o executor deverá utilizar exatamente este identificador.

    if (
        not isinstance(run_id, str)
        or not RUN_ID_PATTERN.fullmatch(run_id)
    ):
        raise ValueError(
            "Identificador de execução inválido."
        )

    return f"ke24_protected_{run_id}"

def reconcile_registered_run(
    *,
    run_journal,
    lock_observation,
    job_observation,
    manifest_status,
):
    #Avalia evidências de uma execução.
    if not isinstance(lock_observation, LockObservation):
        raise ValueError(
            "Evidência do lock inválida."
        )

    if lock_observation.status not in LOCK_STATES:
        raise ValueError(
            "Estado do lock inválido."
        )

    if not isinstance(job_observation, JobObservation):
        raise ValueError(
            "Evidência do job inválida."
        )

    if job_observation.status not in JOB_STATES:
        raise ValueError(
            "Estado do job inválido."
        )

    if manifest_status not in MANIFEST_STATES:
        raise ValueError(
            "Estado do manifesto inválido."
        )

    run_id = run_journal.run_id
    job_id = expected_job_id(run_id)

    if job_observation.job_id != job_id:
        return ExecutionDecision(
            action="BLOCK",
            reason=(
                "Identificador do job não corresponde à execução."
            ),
        )

    # Tenta recuperar o registro persistido.
    try:
        record, journal_generation = run_journal.read()

    except Exception:
        return ExecutionDecision(
            action="RECONCILE",
            reason=(
                "Diário não pôde ser confirmado. Revisão manual necessária."
            ),
        )

    # Valida a identidade e os metadados.
    if (
        not isinstance(record, dict)
        or record.get("run_id") != run_id
        or record.get("state") not in {
            "RUNNING",
            "UNKNOWN",
        }
        or type(record.get("lock_generation")) is not int
        or record["lock_generation"] <= 0
        or type(journal_generation) is not int
        or journal_generation <= 0
    ):
        return ExecutionDecision(
            action="RECONCILE",
            reason=(
                "Registro persistido inválido ou inconclusivo."
            ),
        )

    # Lock ausente ou com resultado incerto não permite concluir a recuperação.
    if lock_observation.status in {
        "MISSING",
        "UNKNOWN",
    }:
        return ExecutionDecision(
            action="RECONCILE",
            reason=(
                "Presença do lock não confirmada. Intervenção necessária."
            ),
        )

    # Verifica quem possui o lock atual.
    if (
        lock_observation.owner_run_id != run_id
        or type(lock_observation.generation) is not int
        or lock_observation.generation
        != record["lock_generation"]
    ):
        return ExecutionDecision(
            action="BLOCK",
            reason=(
                "Lock pertence a outra execução ou geração."
            ),
        )

    # Enquanto houver um job ativo, a execução deve continuar aguardando.
    if job_observation.status == "RUNNING":
        return ExecutionDecision(
            action="WAIT",
            reason="Job permanece em execução.",
        )

    # Um job não encontrado não comprova que nenhuma escrita aconteceu.
    if job_observation.status in {
        "FAILED",
        "UNKNOWN",
        "NOT_FOUND",
    }:
        return ExecutionDecision(
            action="RECONCILE",
            reason=(
                "Resultado do job não comprova conclusão segura."
            ),
        )

    # Neste ponto o job foi informado como SUCCEEDED. Ainda é necessário conferir o resultado do manifesto.
    if manifest_status == "CONFLICT":
        return ExecutionDecision(
            action="BLOCK",
            reason="Manifesto apresenta conflito.",
        )

    if manifest_status == "ALREADY_LOADED":
        return ExecutionDecision(
            action="REVIEW_REQUIRED",
            reason=(
                "Conclusão candidata: conferir o lote antes da liberação manual."
            ),
        )

    return ExecutionDecision(
        action="RECONCILE",
        reason=(
            "Conclusão do job sem confirmação do lote no manifesto."
        ),
    )
