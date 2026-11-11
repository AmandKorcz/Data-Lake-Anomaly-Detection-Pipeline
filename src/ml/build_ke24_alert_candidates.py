from pathlib import Path

import pandas as pd

import numpy as np

from src.ml.ke24_record_identity import (
    IDENTITY_COLUMNS,
    validate_record_identity,
)

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


#Threshold técnico experimental
EXPERIMETAL_ANOMALY_PERCENTILE_MIN = 95.0

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
            "dominant_measure_value",
            "company_code",
            "currency",
            "currency_type"
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

    validate_record_identity(materiality)
    validate_record_identity(stability)


def calculate_grouped_anomaly_percentile(
    dataset,
    min_group_size=20,
):
    required = {
        "company_code",
        "currency",
        "currency_type",
        "anomaly_score",
    }

    missing = required - set(dataset.columns)

    if missing:
        raise ValueError(
            f"Missing candidate fields: {sorted(missing)}"
        )

    if min_group_size < 20:
        raise ValueError("Minimum group size must be at least 20")

    companies = (
        dataset["company_code"]
        .astype("string")
        .str.strip()
    )

    currencies = (
        dataset["currency"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    currency_types = pd.to_numeric(
        dataset["currency_type"],
        errors="coerce",
    )

    scores = pd.to_numeric(
        dataset["anomaly_score"],
        errors="coerce",
    )

    if not companies.str.fullmatch(
        r"TKN_[0-9a-f]{32}"
    ).fillna(False).all():
        raise ValueError("Invalid company tokens")

    if not currencies.str.fullmatch(
        r"[A-Z]{3}"
    ).fillna(False).all():
        raise ValueError("Invalid currency")

    if not currency_types.eq(10).all():
        raise ValueError("Expected currency_type 10")

    if not np.isfinite(
        scores.to_numpy(dtype=float)
    ).all():
        raise ValueError("Invalid anomaly scores")

    grouped_data = pd.DataFrame({
        "company_code": companies,
        "currency": currencies,
        "score": scores,
    }, index=dataset.index)

    group_sizes = (
        grouped_data
        .groupby(["company_code", "currency"])["score"]
        .transform("size")
    )

    if group_sizes.lt(min_group_size).any():
        raise ValueError(
            "Group has insufficient training records"
        )

    return (
        grouped_data
        .groupby(["company_code", "currency"])["score"]
        .rank(method="average", pct=True)
        .mul(100)
    )

#Construção dos candidatos
def build_alert_candidates(materiality, stability):

    validate_inputs(materiality, stability)

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

    #Percentil técnico baseado no anomaly_score
    result["anomaly_score_percentile"] = (
        calculate_grouped_anomaly_percentile(result)
    )

    #Percentual de execuções em que o registro foi marcado como anômalo
    result[
        "stability_rate"
    ] = (
        result["flag_count"] / 5 * 100
    )

    #Seleção experimental dos registros mais relevantes
    candidate_mask = (
        result["anomaly_score_percentile"]
        >= EXPERIMETAL_ANOMALY_PERCENTILE_MIN
    )

    candidates = result.loc[candidate_mask].copy()

    candidates["candidate_threshold_percentile"] = EXPERIMETAL_ANOMALY_PERCENTILE_MIN

    candidates["candidate_reason"] = (
        "anomaly_score_percentile >= "
        + str(EXPERIMETAL_ANOMALY_PERCENTILE_MIN)
    )

    candidates["candidate_status"] = "pending_business_validation"

    candidates = candidates.sort_values(
        [
            "anomaly_score_percentile",
            "stability_rate",
            "materiality_percentile",
            "anomaly_rank",
            "company_code",
            "currency",
        ],
        ascending=[
            False,
            False,
            False,
            True,
            True,
            True,
        ],
    )

    return candidates

#Exibição
def print_candidates_summary(result):

    print(F"\nThreshold técnico experimental: P{EXPERIMETAL_ANOMALY_PERCENTILE_MIN:g}")

    print(f"Registros selecionados: {len(result):,}")

    print("\nCandidatos selecionados para validação de negócio: \n")

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
        "dominant_measure_value",
        "candidate_status"
    ]

    print(
        result[columns]
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

    if result.empty:
        raise ValueError(
            f"Nenhum candidato foi encontrado para P{EXPERIMETAL_ANOMALY_PERCENTILE_MIN}"
        )

    result.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print_candidates_summary(result)

    print(f"\nDataset de candidatos gerado em: ")
    print(OUTPUT_FILE)

if __name__ == "__main__":
    main()