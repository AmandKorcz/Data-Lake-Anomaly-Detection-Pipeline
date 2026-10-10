from dataclasses import dataclass

JOB_STATES = {
    "NEW",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "UNKNOWN",
}

MANIFEST_STATES = {
    "UNVERIFIED",
    "NEW_FILES",
    "ALREADY_LOADED",
    "CONFLICT",
}

@dataclass(frozen=True)
class ExecutionDecision:
    action: str
    reason: str

def decide_execution(
    *,
    policy_status,
    job_status,
    manifest_status,
    exclusive_lock_acquired,
):

    #Avalia se uma tentativa pode avançar
    if job_status not in JOB_STATES:
        raise ValueError(
            "Estado de execução desconhecido."
        )

    if manifest_status not in MANIFEST_STATES:
        raise ValueError(
            "Estado do manifesto desconhecido."
        )

    if not isinstance(exclusive_lock_acquired, bool):
        raise ValueError(
            "Estado do controle de exclusividade inválido."
        )

    # A política precisa ser aprovada
    if policy_status != "APPROVED":
        return ExecutionDecision(
            action="BLOCK",
            reason="Política de privacidade não aprovada.",
        )

    # Uma execução continua em andamento
    if job_status == "RUNNING":
        return ExecutionDecision(
            action="WAIT",
            reason="Existe uma execução em andamento.",
        )

    # Falha ou resultado desconhecido não permite repetir a gravação automaticamente
    if job_status in {"FAILED", "UNKNOWN"}:
        return ExecutionDecision(
            action="RECONCILE",
            reason=(
                "Verificar o resultado da tentativa anterior antes de qualquer reexecução."
            ),
        )

    # Conflitos identificados no manifesto
    if manifest_status == "CONFLICT":
        return ExecutionDecision(
            action="BLOCK",
            reason="Conflito entre arquivos ou períodos.",
        )

    # Sem manifesto atualizado, não há decisão segura
    if manifest_status == "UNVERIFIED":
        return ExecutionDecision(
            action="RECONCILE",
            reason="Manifesto remoto precisa ser verificado.",
        )

    # Dados já existentes não devem ser recarregados
    if manifest_status == "ALREADY_LOADED":
        return ExecutionDecision(
            action="SKIP",
            reason="Arquivos já estão registrados no destino.",
        )

    # Um job concluído não deveria apresentar seus próprios arquivos como inteiramente novos
    if job_status == "SUCCEEDED":
        return ExecutionDecision(
            action="BLOCK",
            reason=(
                "Execução concluída com registros ausentes no manifesto. Revisão necessária."
            ),
        )

    # Exige confirmação de exclusividade externa
    if not exclusive_lock_acquired:
        return ExecutionDecision(
            action="WAIT",
            reason=(
                "Exclusividade da execução ainda não confirmada."
            ),
        )

    # Não libera uma gravação diretamente, apenas encaminha para revisão operacional.
    return ExecutionDecision(
        action="REVIEW_REQUIRED",
        reason=(
            "Planejamento consistente. Revisão operacional necessária antes da carga."
        ),
    )
