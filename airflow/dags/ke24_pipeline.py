
from datetime import datetime

from airflow.sdk import dag, task


@dag(
    dag_id="ke24_anomaly_detection_pipeline",
    description="Orquestracao do pipeline KE24",
    start_date=datetime(2026, 11, 1),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    tags=["KE24", "Data Lake", "Machine Learning"],
)
def ke24_pipeline():
  
    
    @task
    def ingestao():
        import os
        import subprocess

        print("Iniciando verificacao incremental do KE24...")

        resultado = subprocess.run(
            [
                "/opt/ke24-venv/bin/python",
                "-m",
                "src.pipeline.run_ke24_drive_ingestion",
            ],
            cwd="/opt/ke24",
            env={
                **os.environ,
                "PYTHONPATH": "/opt/ke24",
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )

        if resultado.returncode != 0:
            print(f"Codigo de retorno: {resultado.returncode}")
            raise RuntimeError(
                "Falha na ingestao incremental do KE24. "
                "Verifique as configuracoes e os logs locais."
            )

        # Evita expor dados financeiros nos logs do Airflow.
        print("Verificacao incremental concluida com sucesso.")
        print("Nenhum parametro de publicacao foi utilizado.")


    @task
    def validacao():
        import os
        import subprocess

        print("Validando configuracoes de privacidade e qualidade...")

        codigo = """
from src.privacy.privacy_runtime import load_privacy_runtime
from src.quality.validate_ke24 import load_schema, load_quality_rules

policy, schema, key = load_privacy_runtime()
load_schema()
load_quality_rules()

print("Privacidade, schema e regras de qualidade: OK")
"""

        resultado = subprocess.run(
            ["/opt/ke24-venv/bin/python", "-c", codigo],
            cwd="/opt/ke24",
            env={
                **os.environ,
                "PYTHONPATH": "/opt/ke24",
            },
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

        if resultado.returncode != 0:
            print(f"Codigo de retorno: {resultado.returncode}")
            raise RuntimeError(
                "Falha na validacao das configuracoes do KE24."
            )

        print("Validacao das configuracoes concluida com sucesso.")



    @task
    def processamento():
        import os
        import subprocess

        print("Iniciando Feature Engineering do KE24...")

        resultado = subprocess.run(
            [
                "/opt/ke24-venv/bin/python",
                "-m",
                "src.pipeline.run_ke24_drive_features",
            ],
            cwd="/opt/ke24",
            env={
                **os.environ,
                "PYTHONPATH": "/opt/ke24",
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )

        if resultado.returncode != 0:
            print(f"Codigo de retorno: {resultado.returncode}")
            raise RuntimeError(
                "Falha no Feature Engineering do KE24. "
                "Consulte os logs e valide as configuracoes."
            )

        print("Feature Engineering concluido com sucesso.")
        print("Dados processados somente em memoria.")


    @task
    def machine_learning():
        import os
        import subprocess

        print("Iniciando Machine Learning do KE24...")

        resultado = subprocess.run(
            [
                "/opt/ke24-venv/bin/python",
                "-m",
                "src.pipeline.run_ke24_drive_model",
            ],
            cwd="/opt/ke24",
            env={
                **os.environ,
                "PYTHONPATH": "/opt/ke24",
            },
            capture_output=True,
            text=True,
            timeout=1200,
            check=False,
        )

        if resultado.returncode != 0:
            print(f"Codigo de retorno: {resultado.returncode}")
            raise RuntimeError(
                "Falha no Machine Learning do KE24. "
                "Consulte os logs e valide as configuracoes."
            )

        print("Machine Learning concluido com sucesso.")
        print("Treinamento e inferencia executados em memoria.")
        print("Nenhum modelo ou alerta foi publicado.")



    @task
    def alertas():
        import os
        import subprocess

        print("Iniciando previa dos candidatos a alertas do KE24...")

        resultado = subprocess.run(
            [
                "/opt/ke24-venv/bin/python",
                "-m",
                "src.pipeline.run_ke24_drive_alert_preview",
            ],
            cwd="/opt/ke24",
            env={
                **os.environ,
                "PYTHONPATH": "/opt/ke24",
            },
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )

        if resultado.returncode != 0:
            print(f"Codigo de retorno: {resultado.returncode}")
            raise RuntimeError(
                "Falha na previa de alertas do KE24. "
                "Consulte os logs e valide as configuracoes."
            )

        print("Previa de candidatos a alertas concluida.")
        print("Nenhum alerta foi publicado.")
        print("Classificacao financeira e revisao humana permanecem separadas.")



    @task
    def dashboard():
        import os
        import subprocess

        print("Validando a fonte de dados do dashboard KE24...")

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

print("Historico protegido: OK")
print("Alertas no historico:", len(state.history))
print("Registros preparados para o Looker:", len(matrix) - 1)
print("Fonte do dashboard validada. Nenhuma publicacao realizada.")
"""

        resultado = subprocess.run(
            ["/opt/ke24-venv/bin/python", "-c", codigo],
            cwd="/opt/ke24",
            env={
                **os.environ,
                "PYTHONPATH": "/opt/ke24",
            },
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )

        if resultado.returncode != 0:
            print(f"Codigo de retorno: {resultado.returncode}")
            raise RuntimeError(
                "Falha na validacao da fonte do dashboard KE24."
            )

        print(resultado.stdout)
        print("Verificacao do dashboard concluida com sucesso.")


    (
        ingestao()
        >> validacao()
        >> processamento()
        >> machine_learning()
        >> alertas()
        >> dashboard()
    )


ke24_pipeline()

