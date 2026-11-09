from pathlib import Path
import pandas as pd

from src.alerts.validate_ke24_dashboard_export import (
    validate_dashboard_export,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_ROOT / "data" / "curated"
    / "ke24_alert_history.csv"
)

OUTPUT_FILE = (
    PROJECT_ROOT / "data" / "curated"
    / "ke24_dashboard_alerts.csv"
)

DASHBOARD_COLUMNS = [
    "alert_id",
    "period_key",
    "company_code",
    "profit_center",
    "product",
    "reference_document",
    "currency",
    "alert_level",
    "alert_reason",
    "classification_status",
    "investigation_status",
    "anomaly_score",
    "anomaly_score_percentile",
    "anomaly_rank",
    "total_absolute_value",
    "materiality_percentile",
    "stability_rate",
    "dominant_measure",
    "dominant_measure_value",
    "detected_at_utc",
    "updated_at_utc",
]


def build_dashboard_dataset(history):

    if history.empty:
        raise ValueError("O histórico de alertas está vazio.")

    missing = [
        column
        for column in DASHBOARD_COLUMNS
        if column not in history.columns
    ]

    if missing:
        raise ValueError(
            "Colunas ausentes no histórico: "
            + ", ".join(missing)
        )

    if history["alert_id"].duplicated().any():
        raise ValueError("Existem alertas duplicados.")

    result = history[DASHBOARD_COLUMNS].copy()

    # Valida se os identificadores estão protegidos
    token_pattern = r"TKN_[0-9a-f]{32}"

    for column in [
        "company_code",
        "profit_center",
        "product",
        "reference_document",
    ]:
        values = result[column].astype("string")

        valid = (
            values.isna()
            | values.str.fullmatch(token_pattern).fillna(False)
        )

        if not valid.all():
            raise ValueError(
                f"Identificadores não protegidos: {column}"
            )

    # Converte indicadores para valores numéricos
    numeric_columns = [
        "anomaly_score",
        "anomaly_score_percentile",
        "anomaly_rank",
        "total_absolute_value",
        "materiality_percentile",
        "stability_rate",
        "dominant_measure_value",
    ]

    for column in numeric_columns:
        original = result[column]
        numeric = pd.to_numeric(original, errors="coerce")

        if (original.notna() & numeric.isna()).any():
            raise ValueError(
                f"Valores numéricos inválidos: {column}"
            )

        result[column] = numeric

    # Cria uma data para agrupamento no dashboard
    detection_dates = pd.to_datetime(
        result["detected_at_utc"],
        utc=True,
        errors="coerce",
    )

    if detection_dates.isna().any():
        raise ValueError("Datas de detecção inválidas.")

    result["detection_date"] = (
        detection_dates.dt.strftime("%Y-%m-%d")
    )

    result["alert_count"] = 1

    # Valida o contrato final antes da exportacao
    validate_dashboard_export(result)

    return result


def main():
    print("\nKE24 - Base do Dashboard\n")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Histórico não encontrado: {INPUT_FILE}"
        )

    history = pd.read_csv(
        INPUT_FILE,
        encoding="utf-8-sig",
    )

    dashboard = build_dashboard_dataset(history)

    dashboard.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"Alertas exportados: {len(dashboard)}")
    print(f"Arquivo: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
