from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

from airflow.sdk import dag, task

PROJECT_DIR = Path("/opt/airflow/project")

OUTPUT_FILE = (
    PROJECT_DIR
    / "data"
    / "curated"
    / "ke24_dashboard_alerts_modelo_sintetico.csv"
)


@dag(
    dag_id="ke24_synthetic_pipeline",
    start_date=datetime(2026, 11, 1, tzinfo=timezone.utc),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    tags=["ke24", "machine-learning", "synthetic"],
)
def ke24_synthetic_pipeline():

    @task
    def check_environment():
        required = [
            PROJECT_DIR / "src" / "pipeline"
            / "run_ke24_synthetic.py",
            PROJECT_DIR / "config" / "ke24_schema.json",
            PROJECT_DIR / "config" / "ke24_privacy_policy.json",
        ]

        for path in required:
            if not path.is_file():
                raise FileNotFoundError(
                    f"Arquivo necessário ausente: {path.name}"
                )

        print("Ambiente do pipeline validado.")

    @task
    def run_ml_pipeline():
        subprocess.run(
            [
                sys.executable,
                "-m",
                "src.pipeline.run_ke24_synthetic",
            ],
            cwd=PROJECT_DIR,
            check=True,
        )

        print("Pipeline sintético executado.")

    @task
    def validate_dashboard_export():
        import pandas as pd

        if not OUTPUT_FILE.is_file():
            raise FileNotFoundError(
                "CSV de saída não foi gerado."
            )

        dataset = pd.read_csv(OUTPUT_FILE)

        if dataset.empty:
            raise ValueError(
                "O dashboard não recebeu alertas."
            )

        if dataset["alert_id"].duplicated().any():
            raise ValueError(
                "Existem alertas duplicados."
            )

        for column in (
            "company_code",
            "profit_center",
            "product",
            "reference_document",
        ):
            valid = (
                dataset[column]
                .astype("string")
                .str.fullmatch(r"TKN_[0-9a-f]{32}")
                .fillna(False)
            )

            if not valid.all():
                raise ValueError(
                    f"Identificador não protegido: {column}"
                )

        print(
            f"Exportação validada: {len(dataset)} alertas."
        )

    check_environment() >> run_ml_pipeline() >> validate_dashboard_export()


ke24_synthetic_pipeline()
