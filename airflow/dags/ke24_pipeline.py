from datetime import datetime, timedelta
import re

from airflow.providers.standard.operators.hitl import HITLOperator
from airflow.sdk import dag, task

from ke24_branch_rules import PUBLICAR, SOMENTE_PREVIA, selecionar_destino
from ke24_notifications import notificar_falha
from ke24_task_runner import executar_modulo

PREVIA = """
    from tools.ke24_full_flow import execute

    resultado = execute(auto=True)
    if resultado.completed:
        print(f"RESUMO KE24: previa concluida. Alertas: {resultado.alerts}.")
    else:
        print(f"RESUMO KE24: previa chegou a etapa {resultado.paused_at}.")
"""


@dag(
    dag_id="ke24_anomaly_detection_pipeline",
    description="Valida extracoes KE24 e publica alertas com aprovacao no Airflow",
    start_date=datetime(2026, 11, 1),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 0, "on_failure_callback": notificar_falha},
    tags=["KE24", "Data Lake", "Machine Learning"],
)
def ke24_pipeline():
    @task
    def preflight():
        output = executar_modulo(
            modulo="tools.run_ke24_ipv4",
            argumentos=("tools.ke24_new_company_preflight", "--batch-fingerprint"),
            descricao="Preflight KE24",
            timeout=600,
        )
        patterns = {
            "extracoes": r"^Extracoes novas: (\d+)$",
            "registros": r"^Registros novos: (\d+)$",
            "lote": r"^Impressao digital do lote: ([0-9a-f]{64})$",
        }
        campos = {key: re.findall(pattern, output, re.MULTILINE) for key, pattern in patterns.items()}
        if "Preflight E2E: APROVADO" not in output.splitlines() or any(
            len(values) != 1 for values in campos.values()
        ):
            raise RuntimeError("Preflight KE24 sem confirmacao ou resumo valido.")
        return {
            "extracoes": int(campos["extracoes"][0]),
            "registros": int(campos["registros"][0]),
            "lote": campos["lote"][0],
        }

    @task.branch
    def decidir_fluxo(resposta):
        return selecionar_destino(resposta)

    @task
    def somente_previa():
        output = executar_modulo(codigo=PREVIA, descricao="Previa KE24", timeout=3600)
        for line in output.splitlines():
            if line.startswith("RESUMO KE24: "):
                print(line)

    @task
    def publicar_lote(preflight_result, resposta):
        if selecionar_destino(resposta) != "publicar_lote":
            raise RuntimeError("Publicacao KE24 sem aprovacao.")

        codigo = f"""
            from tools.ke24_full_flow import execute

            result = execute(guided=True, auto=True, verbose=True, approved_batch={preflight_result["lote"]!r})
            if not result.completed:
                raise RuntimeError("Publicacao KE24 incompleta.")
            print(f"RESUMO KE24: alertas na fila: {{result.alerts}}. Publicacoes: {{len(result.writes)}}.")
        """
        output = executar_modulo(codigo=codigo, descricao="Publicacao KE24 conferida", timeout=7200)
        for line in output.splitlines():
            if line.startswith("RESUMO KE24: "):
                print(line)

    lote = preflight()
    aprovacao = HITLOperator(
        task_id="aprovar_publicacao",
        subject="KE24: autorizar a publicacao desta execucao?",
        body=(
            "Extracoes novas: {{ ti.xcom_pull(task_ids='preflight')['extracoes'] }}. "
            "Lancamentos novos: {{ ti.xcom_pull(task_ids='preflight')['registros'] }}. "
            "A autorizacao inclui processed, historico de alertas, fila e contexto no Google Sheets, "
            "e a substituicao do snapshot completo no BigQuery "
            "(datalake-pipeline.profitability_data.ke24_processed). "
            "Os alertas novos sao calculados durante a execucao. "
            "Para evitar gravacoes, escolha Somente previa."
        ),
        options=[PUBLICAR, SOMENTE_PREVIA],
        defaults=[SOMENTE_PREVIA],
        response_timeout=timedelta(hours=1),
    )
    lote >> aprovacao
    decidir_fluxo(aprovacao.output) >> [somente_previa(), publicar_lote(lote, aprovacao.output)]


ke24_pipeline()
