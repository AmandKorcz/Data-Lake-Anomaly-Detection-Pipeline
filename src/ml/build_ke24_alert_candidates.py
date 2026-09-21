from pathlib import Path

import pandas as pd

#Configurações
PROJECT_ROOT = Path(__file__).resolve().parents[2]

MATERIALITY_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_anomaly_materiality.csv"
)

STABILITY_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_isolation_forest_stability.csv"
)

OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_alert_candidates.csv"
)

IDENTITY_COLUMNS = [
    "_source_file",
    "_source_row_number"
]

#Validação
def validate_inputs(materiality, stability):

    required_materiality = (
        IDENTITY_COLUMNS
        + [
            "anomaly_rank",
            "anomaly_flag",
            "anomaly_score",
            "period_key",
            "reference_document",
            "profit_center",
            "product",
            "total_absolute_value",
            "materiality_percentile",
            "dominant_measure",
            "dominant_measure_value"
        ]
    )

    missing_materiality = [
        column 
        for column in required_materiality
        if column not in materiality.columns
    ]

    if missing_materiality:
        raise ValueError(
            "Colunas ausentes na análise de materialidade: "
            +", ".join(missing_materiality)
        )

    required_stability = (
        IDENTITY_COLUMNS
        + [
            "mean_anomaly_score",
            "score_std",
            "flag_count",
            "mean_rank"
        ]
    )

    missing_stability = [
        column 
        for column in required_stability
        if column not in stability.columns
    ]

    if missing_stability:
        raise ValueError(
            "Colunas ausentes na análise de estabilidade: "
            +", ".join(missing_stability)
        )

#Construção dos candidatos
def build_alert_candidates(materiality, stability):

    stability_columns = (
        IDENTITY_COLUMNS
        + [
            "mean_anomaly_score",
            "score_std",
            "flag_count",
            "mean_rank"
        ]
    )

    result = materiality.merge(
        stability[stability_columns],
        on=IDENTITY_COLUMNS,
        how="left",
        validate="one_to_one"
    )

    if result["mean_anomaly_score"].isna().any():
        raise ValueError("Existem registros sem dados de estabilidade.")

    result[
        "anomaly_score_percentile"
    ] = (
        result["anomaly_score"].rank(
            method="average",
            pct=True
        )
        * 100
    )

    result[
        "stability_rate"
    ] = (
        result["flag_count"] / 5 * 100
    )

    result = result.sort_values(
        [
            "anomaly_rank",
            "materiality_percentile"
        ],
        ascending=[
            True,
            False
        ],
    )

    return result

#Exibição
def print_top_candidates(result):
    print("\nTop 10 candidatos para revisão: \n")

    columns = [
        "anomaly_rank",
        "anomaly_score",
        "anomaly_score_percentile",
        "materiality_percentile",
        "stability_rate",
        "period_key",
        "reference_document",
        "profit_center",
        "total_absolute_value",
        "dominant_measure",
        "dominant_measure_value"
    ]

    print(
        result[columns]
        .head(10)
        .to_string(index=False)
    )

#Pipeline principal
def main():
    print("\nKE24 - Construção dos Candidatos a Alerta\n")

    if not MATERIALITY_FILE.exists():
        raise FileNotFoundError(
            f"Arquivo de materialidade não encontrado: {MATERIALITY_FILE}"
        )

    if not STABILITY_FILE.exists():
        raise FileNotFoundError(
            f"Arquivo de estabilidade não encontrado: {STABILITY_FILE}"
        )

    materiality = pd.read_csv(
        MATERIALITY_FILE,
        encoding="utf-8-sig"
    )

    stability = pd.read_csv(
        STABILITY_FILE,
        encoding="utf-8-sig"
    )

    validate_inputs(materiality, stability)
    result = build_alert_candidates(materiality, stability)

    result.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print(f"Registros consolidados: {len(result):,}")
    print_top_candidates(result)

    print(f"\nDataset de candidatos gerado em: ")
    print(OUTPUT_FILE)

if __name__ == "__main__":
    main()