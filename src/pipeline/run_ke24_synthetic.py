import io
import json
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.privacy.anonymize_ke24 import anonymize_ke24_dataframe
from src.privacy.anonymize_ke24 import generate_token

from src.processing import prepare_ke24_processed as processing
from src.features import build_ke24_ml_features as features

from src.ml import prepare_ke24_training_matrix as training
from src.ml import train_ke24_isolation_forest as detection
from src.ml import profile_ke24_anomaly_materiality as materiality
from src.ml import evaluate_ke24_isolation_forest_stability as stability
from src.ml import build_ke24_alert_candidates as candidates
from src.ml import build_ke24_business_validation_dataset as business
from src.ml import classify_ke24_alerts as classification

from src.ml.protect_ke24_alert_rules import protect_company_rule_keys

from src.alerts.build_ke24_alert_records import build_alert_records

from src.alerts.persist_ke24_alert_history import (
    enrich_alert_records,
    merge_alert_history,
)

from src.alerts.build_ke24_dashboard_dataset import (
    build_dashboard_dataset,
)

from src.privacy.validate_protected_dataset import (
    validate_protected_dataset,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

OUTPUT_FILE = (
    PROJECT_ROOT / "data" / "curated"
    / "ke24_dashboard_alerts_modelo_sintetico.csv"
)

HISTORY_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_alert_history_synthetic.csv"
)

# Chave fixa exclusivamente para dados fictícios.
# Nunca utilizar esta chave com dados corporativos.
TEST_KEY = b"CHAVE_FICTICIA_APENAS_PARA_TESTES_2026"


def load_json(filename):
    with (
        PROJECT_ROOT / "config" / filename
    ).open(encoding="utf-8") as file:
        return json.load(file)


def generate_synthetic_data(schema):
    columns = schema["columns"]
    start = columns.index("sales_quantity")

    dimensions = columns[:start]
    measures = columns[start:]
    records = []

    for index in range(40):
        company = "COMP_A" if index < 20 else "COMP_B"
        period = index % 12 + 1

        record = {
            "_load_id": "SYNTHETIC_LOAD",
            "_source_file_sha256": "f" * 64,
            "_source_system": "SAP_KE24",
            "_source_file": "synthetic_ke24.xlsx",
            "_source_row_number": index + 2,
            "_ingested_at_utc": pd.Timestamp(
                "2026-01-01T00:00:00Z"
            ),
        }

        record.update({
            column: "EXEMPLO"
            for column in dimensions
        })

        record.update({
            column: 0.0
            for column in measures
        })

        record.update({
            "company_code": company,
            "currency_type": 10,
            "currency": "BRL",
            "period": period,
            "period_year": f"{period:02d}.2026",
            "profit_center": f"CENTRO_{company}",
            "product": f"PROD_{index % 3}",
            "reference_document": f"DOC_{index:03d}",
            "sales_quantity": float(index % 3 + 1),
            "revenue": (
                200000.0 if index == 39
                else float(500 + (index % 13) * 29)
            ),
            "c_4rm_copper_wire": -float(
                40 + (index % 7) * 5
            ),
        })

        records.append(record)

    return pd.DataFrame(records)


def build_synthetic_rules():
    return {
        "status": "experimental_auto_classification",
        "currency_basis": {"currency_type": 10},
        "business_thresholds_by_company": {
            company: {
                "currency": "BRL",
                "materiality_medium_min": 100.0,
                "materiality_high_min": 300.0,
            }
            for company in ("COMP_A", "COMP_B")
        },
        "technical_thresholds": {
            "anomaly_percentile_medium_min": 90,
            "anomaly_percentile_high_min": 95,
            "stability_medium_min": 40,
            "stability_high_min": 100,
        },
    }


def run_pipeline():
    print("\nKE24 - Pipeline Sintético\n")

    schema = load_json("ke24_schema.json")
    policy = load_json("ke24_privacy_policy.json")

    # 1. Dados fictícios e pseudonimização
    raw = generate_synthetic_data(schema)

    protected = anonymize_ke24_dataframe(
        raw, policy, schema, TEST_KEY
    )

    print(f"1. Dados protegidos: {len(protected)} registros")

    # 2. Camada processed
    processing.validate_input_data(protected)

    processed, _ = processing.standardize_measure_types(
        protected.copy()
    )

    processed = processing.add_period_key(processed)
    processed = processing.sort_processed_data(processed)

    processing.validate_processed_data(
        processed, len(protected)
    )

    print("2. Processamento concluído")

    # 3. Feature engineering
    measure_columns = features.get_measure_columns(processed)

    measures = features.prepare_measure_matrix(
        processed, measure_columns
    )

    active, _ = features.select_active_measures(measures)
    engineered = features.build_engineered_features(measures)

    ml_features = pd.concat([
        processed[features.CONTEXT_COLUMNS].reset_index(drop=True),
        measures[active].reset_index(drop=True),
        engineered.reset_index(drop=True),
    ], axis=1)

    features.validate_output(
        processed, ml_features, list(engineered.columns)
    )

    manifest = features.build_feature_manifest(
        measure_columns,
        active,
        list(engineered.columns),
    )

    print("3. Features geradas")

    # 4. Matriz e Isolation Forest
    selected = training.get_selected_features(manifest)

    matrix = training.build_training_matrix(
        ml_features, selected
    )

    # Remove atributos constantes
    matrix = matrix.loc[
        :, matrix.nunique(dropna=False).gt(1)
    ]

    if matrix.empty or len(matrix.columns) == 0:
        raise ValueError("Matriz de treinamento inválida")

    detection.validate_inputs(matrix, ml_features)

    model = detection.train_isolation_forest(matrix)
    scores = detection.build_anomaly_scores(model, matrix)

    anomaly_results = detection.build_results(
        ml_features, scores
    )

    print(
        "4. Anomalias sinalizadas:",
        int(anomaly_results["anomaly_flag"].sum())
    )

    # 5. Materialidade
    financial = materiality.get_financial_features(manifest)

    materiality.validate_inputs(
        ml_features, anomaly_results, financial
    )

    materiality_result = materiality.build_materiality_dataset(
        ml_features, anomaly_results, financial
    )

    # 6. Estabilidade
    # Arquivos temporários exclusivos da execução sintética
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)

        matrix_file = directory / "matrix.parquet"
        features_file = directory / "features.parquet"
        stability_file = directory / "stability.csv"

        matrix.to_parquet(matrix_file, index=False)
        ml_features.to_parquet(features_file, index=False)

        with (
            patch.object(stability, "TRAINING_FILE", matrix_file),
            patch.object(stability, "CONTEXT_FILE", features_file),
            patch.object(stability, "OUTPUT_FILE", stability_file),
            redirect_stdout(io.StringIO()),
        ):
            stability.main()

        stability_result = pd.read_csv(stability_file)

    print("5. Materialidade e estabilidade calculadas")

    # 7. Candidatos a alerta
    candidates.validate_inputs(
        materiality_result, stability_result
    )

    selected_candidates = candidates.build_alert_candidates(
        materiality_result, stability_result
    )

    if selected_candidates.empty:
        raise ValueError("Nenhum candidato encontrado")

    business.validate_inputs(
        selected_candidates, ml_features
    )

    # Impede leitura de validações corporativas antigas
    with patch.object(
        business, "load_existing_validation", return_value=None
    ):
        validation = business.build_validation_dataset(
            selected_candidates, ml_features
        )

    # Regras financeiras inteiramente fictícias
    protected_rules = protect_company_rule_keys(
        build_synthetic_rules(), TEST_KEY
    )

    classification.validate_dataset(validation)
    classification.validate_currency_basis(
        validation, protected_rules
    )

    classified = classification.build_classification(
        validation, protected_rules
    )

    print(f"6. Alertas classificados: {len(classified)}")

    
    # 8. Alert records
    records = build_alert_records(
        classified,
        datetime.now(timezone.utc),
    )

    enriched = enrich_alert_records(
        records,
        classified,
    )

    # Load previous synthetic history, when available
    if HISTORY_FILE.exists():
        previous_history = pd.read_csv(
            HISTORY_FILE,
            encoding="utf-8-sig",
        )

        if previous_history.empty:
            raise ValueError(
                "Existing synthetic history is empty"
            )

        validate_protected_dataset(
            previous_history,
            policy,
            require_all_columns=False,
        )

        required_history = {
            "alert_id",
            "investigation_status",
            "detected_at_utc",
        }

        if not required_history.issubset(
            previous_history.columns
        ):
            raise ValueError(
                "Invalid synthetic history schema"
            )

        if previous_history["alert_id"].duplicated().any():
            raise ValueError(
                "Duplicate alert IDs in history"
            )

        print(
            "Previous alerts loaded:",
            len(previous_history),
        )
    else:
        previous_history = pd.DataFrame()
        print("First synthetic execution")

    # Preserve previous investigation status
    history = merge_alert_history(
        previous_history,
        enriched,
    )

    # Validate before writing any files
    validate_protected_dataset(
        history,
        policy,
        require_all_columns=False,
    )

    dashboard = build_dashboard_dataset(history)

    # Persist only synthetic data
    HISTORY_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    history.to_csv(
        HISTORY_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    # 9. Base para Looker Studio
    dashboard = build_dashboard_dataset(history)

    OUTPUT_FILE.parent.mkdir(
        parents=True, exist_ok=True
    )

    dashboard.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"7. Registros para dashboard: {len(dashboard)}")
    print("\nPIPELINE SINTÉTICO CONCLUÍDO")
    print(f"Arquivo: {OUTPUT_FILE}")


if __name__ == "__main__":
    run_pipeline()
