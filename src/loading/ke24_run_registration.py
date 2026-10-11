from src.loading.ke24_execution_control import (
    ExecutionDecision,
)
from src.loading.ke24_run_coordinator import (
    coordinate_execution,
)

def coordinate_and_register(
    *,
    policy_status,
    job_status,
    manifest_status,
    execution_lock,
    run_journal,
):

    # O lock e o diário devem identificar exatamente a mesma execução.
    if execution_lock.run_id != run_journal.run_id:
        raise ValueError(
            "Identificadores de execução incompatíveis."
        )

    # O coordenador valida as condições e tenta adquirir a exclusividade.
    decision = coordinate_execution(
        policy_status=policy_status,
        job_status=job_status,
        manifest_status=manifest_status,
        execution_lock=execution_lock,
    )

    # Não registra tentativas bloqueadas, repetidas ou que exigem reconciliação.
    if decision.action != "REVIEW_REQUIRED":
        return decision

    # Confirma que o lock possui uma geração válida antes do registro.
    generation = execution_lock.generation

    if (
        execution_lock.acquired is not True
        or not isinstance(generation, int)
        or generation <= 0
    ):
        return ExecutionDecision(
            action="RECONCILE",
            reason=(
                "Exclusividade sem geração válida. Reconciliação necessária."
            ),
        )

    try:
        # Registra a tentativa e verifica se o registro foi persistido.
        run_journal.start(execution_lock)

        record, journal_generation = run_journal.read()

    except Exception:
        # A gravação pode ter sido concluída mesmo que a resposta tenha falhado. Não libera o lock.
        return ExecutionDecision(
            action="RECONCILE",
            reason=(
                "Resultado do registro desconhecido. Manter o lock e verificar o diário."
            ),
        )

    # Confere o conteúdo persistido.
    if (
        not isinstance(record, dict)
        or record.get("run_id") != execution_lock.run_id
        or record.get("state") != "RUNNING"
        or record.get("lock_generation") != generation
        or not isinstance(journal_generation, int)
        or journal_generation <= 0
    ):
        return ExecutionDecision(
            action="RECONCILE",
            reason=(
                "Registro persistido inconsistente. Reconciliação necessária."
            ),
        )

    # Ainda não existe autorização de escrita.
    return ExecutionDecision(
        action="REVIEW_REQUIRED",
        reason=(
            "Exclusividade adquirida e tentativa rregistrada. Revisão operacional pendente."
        ),
    )