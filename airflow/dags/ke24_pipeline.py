from datetime import datetime
from ke24_task_runner import executar_modulo, PYTHON_KE24, PROJECT_ROOT
from ke24_branch_rules import selecionar_destino
from ke24_notifications import notificar_falha

from airflow.sdk import dag, task

import os
import re
import subprocess

@dag(
    dag_id="ke24_anomaly_detection_pipeline",
    description="Orquestracao do pipeline KE24",
    start_date=datetime(2026, 11, 1),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 0,
        "on_failure_callback": notificar_falha,
    },
    tags=["KE24", "Data Lake", "Machine Learning"],
)

def ke24_pipeline():

    @task
    def preflight():
        resultado = subprocess.run(
            [
                PYTHON_KE24,
                "-m",
                "tools.ke24_new_company_preflight",
            ],
            cwd=PROJECT_ROOT,
            env={
                **os.environ,
                "PYTHONPATH": PROJECT_ROOT,
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )

        if resultado.returncode != 0:
            raise RuntimeError(
                "Preflight KE24 reprovado. "
                "Verifique a execucao em ambiente restrito."
            )

        if "Preflight E2E: APROVADO" not in resultado.stdout:
            raise RuntimeError(
                "Confirmacao de aprovacao do preflight ausente."
            )

        correspondencia = re.search(
            r"^Extracoes novas:\s*(\d+)\s*$",
            resultado.stdout,
            re.MULTILINE,
        )

        if correspondencia is None:
            raise RuntimeError(
                "Quantidade de novas extracoes nao identificada."
            )

        novas_extracoes = int(correspondencia.group(1))

        print(f"Preflight aprovado | Novas extracoes: {novas_extracoes}")

        return novas_extracoes  

    @task.branch
    def decidir_fluxo(novas_extracoes: int):
        destino = selecionar_destino(novas_extracoes)

        if destino == "validacao":
            print("Preflight aprovado. Seguindo pelo fluxo rapido.")
        else:
            print(
                "Novas extracoes detectadas. "
                "Publicacao manual necessaria."
            )

        return destino
    

    @task
    def aguardando_publicacao():
        raise RuntimeError(
            "Novas extracoes detectadas. "
            "Publicacao manual necessaria antes de continuar. "
            "Nenhuma publicacao foi realizada pelo Airflow."
        )    

    @task
    def validacao():
        codigo = """
            from src.privacy.privacy_runtime import load_privacy_runtime
            from src.quality.validate_ke24 import load_schema, load_quality_rules

            load_privacy_runtime()
            load_schema()
            load_quality_rules()
        """

        executar_modulo(
            codigo=codigo,
            descricao="Validacao de privacidade e qualidade KE24",
            timeout=120,
        )


    @task
    def processamento():
        executar_modulo(
            modulo="src.pipeline.run_ke24_drive_features",
            descricao="Feature Engineering KE24",
            timeout=900,
        )


    @task
    def alertas():
        executar_modulo(
            modulo="src.pipeline.run_ke24_drive_alert_preview",
            descricao="Previa de candidatos a alertas KE24",
            timeout=1800,
        )


    @task
    def dashboard():
        codigo = """
    from googleapiclient.discovery import build
    from src.privacy.privacy_runtime import load_privacy_runtime
    from src.ingestion.google_drive_client import authenticate
    from src.loading.ke24_drive_alert_history import load_alert_history_state
    from src.loading.ke24_looker_sheet import build_matrix

    policy, _, _ = load_privacy_runtime()

    service = build(
        "drive",
        "v3",
        credentials=authenticate(),
        cache_discovery=False,
    )

    state = load_alert_history_state(service, policy)

    if not state.snapshots:
        raise RuntimeError("Historico protegido ainda nao foi publicado.")

    matrix = build_matrix(state.history, policy)

    if len(matrix) != len(state.history) + 1:
        raise RuntimeError("Quantidade de registros inconsistente.")
    """

        executar_modulo(
            codigo=codigo,
            descricao="Validacao da fonte do dashboard KE24",
            timeout=180,
        )

    resultado_preflight = preflight()
    decisao = decidir_fluxo(resultado_preflight)

    tarefa_validacao = validacao()
    tarefa_aguardando = aguardando_publicacao()

    decisao >> [tarefa_validacao, tarefa_aguardando]

    (
        tarefa_validacao
        >> processamento()
        >> alertas()
        >> dashboard()
    )

ke24_pipeline()