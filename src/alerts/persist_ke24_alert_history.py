from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

from src.alerts.build_ke24_alert_records import (
    build_alert_records,
    validate_alert_input,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_alert_classification.csv"
)

OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_alert_history.csv"
)


def merge_alert_history(history, current):
    if history.empty:
        return current.copy()

    # Confere os campos básicos do histórico
    required_columns = {
        "alert_id",
        "detected_at_utc",
        "investigation_status",
    }

    if not required_columns.issubset(history.columns):
        raise ValueError(
            "A base histórica possui campos obrigatórios ausentes."
        )

    # Adiciona campos novos sem apagar registros anteriores
    for column in current.columns:
        if column not in history.columns:
            history[column] = pd.NA 

    validate_alert_input(history)

    if history["alert_id"].duplicated().any():
        raise ValueError(
            "Existem alertas duplicados no histórico."
        )

    current = current.copy()

    previous = history.set_index("alert_id")

    existing_mask = current["alert_id"].isin(
        previous.index
    )

    # Mantém a primeira detecção e o status da investigação
    for column in (
        "detected_at_utc",
        "investigation_status",
    ):
        current.loc[existing_mask, column] = (
            current.loc[existing_mask, "alert_id"]
            .map(previous[column])
        )

    # Mantém alertas antigos que não apareceram nesta execução
    old_records = history.loc[
        ~history["alert_id"].isin(current["alert_id"])
    ]

    result = pd.concat(
        [old_records, current],
        ignore_index=True
    )

    return result.sort_values(
        ["period_key", "alert_id"]
    ).reset_index(drop=True)

    
def enrich_alert_records(current, classification):

    identity_columns = [
        "_source_file_sha256",
        "_source_row_number",
        "company_code",
    ]

    dashboard_columns = [
        "anomaly_score",
        "anomaly_rank",
        "anomaly_score_percentile",
        "total_absolute_value",
        "materiality_percentile",
        "stability_rate",
        "dominant_measure",
        "dominant_measure_value",
        "profit_center",
        "product",
        "currency",
        "currency_type",
        "alert_reason",
    ]

    required = identity_columns + dashboard_columns

    missing = [
        column for column in required
        if column not in classification.columns
    ]

    if missing:
        raise ValueError(
            "Colunas ausentes para o dashboard: "
            + ", ".join(missing)
        )

    details = classification[required].copy()

    return current.merge(
        details,
        on=identity_columns,
        how="left",
        validate="one_to_one",
    )

def main():
    print("\nKE24 - Histórico de Alertas\n")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Classificação não encontrada: {INPUT_FILE}"
        )

    classification = pd.read_csv(
        INPUT_FILE,
        encoding="utf-8-sig"
    )

    current = build_alert_records(
    classification,
    datetime.now(timezone.utc)
    )

    current = enrich_alert_records(
        current,
        classification
    )

    if OUTPUT_FILE.exists():
        history = pd.read_csv(
            OUTPUT_FILE,
            encoding="utf-8-sig"
        )
    else:
        history = pd.DataFrame()

    result = merge_alert_history(history, current)

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    result.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print(f"Alertas nesta execução: {len(current)}")
    print(f"Total no histórico: {len(result)}")
    print(f"Arquivo gerado: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
