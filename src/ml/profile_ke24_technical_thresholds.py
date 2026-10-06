from pathlib import Path

import pandas as pd

#Configurações
PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_business_validation.csv"
)

OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_technical_threshold_profile.csv"
)

ANOMALY_PERCENTILE_SCENARIOS = [
    80,
    90,
    95,
    99
]

#Validação
def validate_dataset(dataset):
    required_columns = [
        "anomaly_score",
        "anomaly_score_percentile",
        "stability_rate",
        "anomaly_rank",
        "period_key",
        "company_code",
        "reference_document",
        "profit_center"
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in dataset.columns
    ]

    if missing_columns:
        raise ValueError(
            "Colunas obrigatórias ausentes: "
            +", ".join(missing_columns)
        )

#Preparação dos valores técnicos
def prepare_numeric_columns(dataset):
    result = dataset.copy()

    numeric_columns = [
        "anomaly_score",
        "anomaly_score_percentile",
        "stability_rate"
    ]

    for column in numeric_columns:
        result[column] = pd.to_numeric(
            result[column],
            errors="coerce"
        )

        if result[column].isna().any():
            raise ValueError(
                f"Existem valores inválidos na colunas {column}"
            )

    invalid_anomaly_percentile = (
        ~result["anomaly_score_percentile"]
        .between(
            0,
            100,
            inclusive="both"
        )
    )

    if invalid_anomaly_percentile.any():
        raise ValueError(
            "Existem anomaly_score_percentile fora do intervalo de 0 a 100"
        )

    invalid_stability = (
        ~result["stability_rate"]
        .between(
            0,
            100,
            inclusive="both"
        )
    )

    if invalid_stability.any():
        raise ValueError(
            "Existem stability_rate fora do intervalo de 0 a 100"
        )

    calculated_percentile = (
            result["anomaly_score"]
            .rank(
                method="average",
                pct=True
            ) * 100
        )
    
    percentile_difference = (
        result["anomaly_score_percentile"] - calculated_percentile
    ).abs()
    
    if percentile_difference.gt(0.000001).any():
        raise ValueError(
            "O anomaly_score_percentile armazenado não corresponde ao percentil calculado a partir do anomaly_score"
        )
    
    result["profile_anomaly_percentile"] = calculated_percentile

    return result

#Perfil do anomaly score
def print_anomaly_score_profile(dataset):

    percentiles = [
        0.50,
        0.75,
        0.90,
        0.95,
        0.99,
        1.00
    ]

    print("\nDistribuição do anomaly_score: \n")

    for percentile in percentiles:
        value = (dataset["anomaly_score"].quantile(percentile))

        label = int(
            percentile * 100
        )

        print(f"P{label}: {value:.6f}")

#Cenários de anomaly percentile
def build_anomaly_scenarios(dataset):

    rows = []

    total_records = len(
        dataset
    )

    for threshold in (
        ANOMALY_PERCENTILE_SCENARIOS
    ):

        count = int(
            dataset[
                "anomaly_score_percentile"
            ]
            .ge(threshold)
            .sum()
        )

        percentage = (
            count
            / total_records
            * 100
        )

        rows.append({
            "analysis_type":
                "anomaly_percentile",

            "threshold":
                threshold,

            "record_count":
                count,

            "record_percentage":
                percentage,
        })

    return rows

#Perfil de estabilidade
def build_stability_profile(dataset):
    rows = []

    total_records = len(dataset)

    stability_counts = (
        dataset[
            "stability_rate"
        ]
        .value_counts()
        .sort_index()
    )

    for stability_rate, count in (
        stability_counts.items()
    ):
        percentage = (int(count) / total_records * 100)

        rows.append({
            "analysis_type": "stability_rate",
            "threshold": float(stability_rate),
            "record_count": int(count),
            "record_percentage": percentage
        })

    return rows

#Exibição dos cenários
def print_anomaly_scenarios(profile):
    anomaly_profile = (
        profile [
            profile["analysis_type"]
            .eq("anomaly_percentile")
        ]
    )

    print("\nCenários exploratórios de anomaly percentile: \n")

    print(
        anomaly_profile[
            [
                "threshold",
                "record_count",
                "record_percentage"
            ]
        ]
        .to_string(index=False)
    )

def print_stability_profile(profile):
    stability_profile = (
        profile[
            profile["analysis_type"]
            .eq("stability_rate")
        ]
    )

    print("\nDistribuição do stability_rate: \n")

    print(
        stability_profile[
            [
                "threshold",
                "record_count",
                "record_percentage"
            ]
        ]
        .to_string(index=False)
    )

#Registros mais relevantes tecnicamente
def print_top_technical_records(dataset):
    top = (
        dataset
        .sort_values(
            [
                "anomaly_score_percentile",
                "stability_rate",
                "anomaly_score"
            ],
            ascending=[
                False,
                False,
                False
            ],
        )
        .head(10)
    )

    print("\nTop 10 registros pelo sinal técnico: \n")

    columns = [
        "anomaly_rank",
        "anomaly_score",
        "anomaly_score_percentile",
        "stability_rate",
        "period_key",
        "company_code",
        "reference_document",
        "profit_center"
    ]

    print(
        top[columns].to_string(index=False)
    )

#Pipeline principal 
def main():
    print("KE24 - Perfil dos Limites Técnicos\n")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Dataset de validação não encontrado: {INPUT_FILE}"
        )

    dataset = pd.read_csv(
        INPUT_FILE,
        encoding="utf-8-sig"
    )

    validate_dataset(dataset)
    
    dataset = (prepare_numeric_columns(dataset))

    profile_rows = []

    profile_rows.extend(build_anomaly_scenarios(dataset))
    profile_rows.extend(build_stability_profile(dataset))

    profile = pd.DataFrame(profile_rows)

    profile.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print(f"Registros analisados: {len(dataset):,}")

    print_anomaly_score_profile(dataset)
    print_anomaly_scenarios(profile)
    print_stability_profile(profile)
    print_top_technical_records(dataset)

    print("\nPerfil técnico gerado em: ")
    print(OUTPUT_FILE)

if __name__ == "__main__":
    main()