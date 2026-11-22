from datetime import datetime
from ke24_task_runner import executar_modulo

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
        executar_modulo(
            modulo="src.pipeline.run_ke24_drive_ingestion",
            descricao="Ingestao incremental KE24",
            timeout=600,
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
    def machine_learning():
        executar_modulo(
            modulo="src.pipeline.run_ke24_drive_model",
            descricao="Machine Learning KE24",
            timeout=1200,
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

    (
        ingestao()
        >> validacao()
        >> processamento()
        >> machine_learning()
        >> alertas()
        >> dashboard()
    )


ke24_pipeline()

