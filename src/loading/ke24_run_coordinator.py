from src.loading.ke24_execution_control import (
    ExecutionDecision,
    decide_execution,
)

def coordinate_execution(
    *,
    policy_status,
    job_status,
    manifest_status,
    execution_lock,
):
    #Coordena uma tentativa de execução
    # Primeiro avalia as regras sem exclusividade
    preliminary = decide_execution(
        policy_status=policy_status,
        job_status=job_status,
        manifest_status=manifest_status,
        exclusive_lock_acquired=False,
    )

    # Bloqueios, reconciliações e arquivos já carregados não precisam adquirir um lock
    if (
        preliminary.action != "WAIT"
        or job_status != "NEW"
        or manifest_status != "NEW_FILES"
        or policy_status != "APPROVED"
    ):
        return preliminary

    # Um lock já adquirido precisa ser reconciliado antes de começar uma nova tentativa
    if execution_lock.acquired:
        return ExecutionDecision(
            action="RECONCILE",
            reason=(
                "A instância já possui exclusividade. "
                "Verifique a tentativa anterior."
            ),
        )

    # Tenta adquirir a exclusividade
    try:
        acquired = execution_lock.acquire()

    except Exception:
        # Uma falha de comunicação pode ocorrer depois que o lock foi criado no GCS
        return ExecutionDecision(
            action="RECONCILE",
            reason=(
                "Resultado da aquisição do lock "
                "desconhecido. Verificação necessária."
            ),
        )

    if acquired is False:
        return ExecutionDecision(
            action="WAIT",
            reason="Outra execução possui a exclusividade.",
        )

    # Exige confirmação explícita da aquisição
    if acquired is not True or execution_lock.acquired is not True:
        return ExecutionDecision(
            action="RECONCILE",
            reason=(
                "Exclusividade não confirmada. "
                "Revisão necessária."
            ),
        )

    # A execução segue apenas para revisão. Este método NÃO autoriza a gravação.
    return decide_execution(
        policy_status=policy_status,
        job_status=job_status,
        manifest_status=manifest_status,
        exclusive_lock_acquired=True,
    )
