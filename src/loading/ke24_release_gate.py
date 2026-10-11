#Entrada protegida de registro KE24; não executa cargas.
#O coordenador genérico continua aceitando estados sintéticos para testes.

from src.loading.ke24_execution_control import ExecutionDecision
from src.loading.load_ke24_bigquery import validate_bigquery_release
from src.loading.ke24_run_registration import coordinate_and_register

def register_protected_run(
    *,
    job_status,
    manifest_status,
    execution_lock,
    run_journal,
):
    try:
        # O validador existente confere o destino protegido, a política aprovada, o contrato e a chave HMAC.
        # O DataFrame não é fornecido: validação de dados é outra etapa.
        validate_bigquery_release()
    except Exception:
        # Não expor detalhes da chave, caminhos locais ou configuração.
        return ExecutionDecision(
            action="BLOCK",
            reason=(
                "Pré-validação de privacidade ou destino não aprovada. Nenhuma tentativa pode ser registrada."
            ),
        )

    # A validação anterior é a única origem autorizada para APPROVED.
    # Nunca confiar em uma string APPROVED passada por código chamador.
    return coordinate_and_register(
        policy_status="APPROVED",
        job_status=job_status,
        manifest_status=manifest_status,
        execution_lock=execution_lock,
        run_journal=run_journal,
    )
