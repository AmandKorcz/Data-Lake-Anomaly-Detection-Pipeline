from pathlib import Path

import pandas as pd

#Configurações
PROJECT_ROOT = Path(__file__).resolve().parents[2]

ALERT_CANDIDATES_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_alert_candidates.csv"
)

ML_FEATURES_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_ml_features.parquet"
)

OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_business_validation.csv"
)

IDENTIFY_COLUMNS = [
    "_source_file",
    "_source_row_number"
]

BUSINESS_VALIDATION_COLUMNS = [
    "review_status",
    "business_validation",
    "validation_reason",
    "validated_by",
    "validated_at",
    "business_notes"
]

#Validação dos arquivos
def validate_inputs(alert_candidates, ml_features):
    required_candidate_columns = (
        IDENTIFY_COLUMNS
        + [
            "anomaly_rank",
            "anomaly_score",
            "anomaly_score_percentile",
            "period_key",
            "company_code",
            "reference_document",
            "profit_center",
            "product",
            "total_absolute_value",
            "materiality_percentile",
            "dominant_measure",
            "dominant_measure_value",
            "mean_anomaly_score",
            "score_std",
            "flag_count",
            "mean_rank",
            "stability_rate"
        ]
    )

    missing_candidate_columns = [
        column 
        for column in required_candidate_columns
        if column not in alert_candidates.columns
    ]

    if missing_candidate_columns:
        raise ValueError(
            "Colunas ausentes no dataset de candidatos a alerta: "
            +", ".join(missing_candidate_columns)
        )

    required_feature_columns = (
        IDENTIFY_COLUMNS
        + [
            "currency",
            "currency_type"
        ]
    )

    missing_feature_columns = [
        column 
        for column in required_feature_columns
        if column not in ml_features.columns
    ]

    if missing_feature_columns:
        raise ValueError(
            "Colunas ausentes no dataser de features: "
            +", ".join(missing_feature_columns)
        )

    if alert_candidates.duplicated(
        subset=IDENTIFY_COLUMNS
    ).any():
        raise ValueError("Existem registros duplicados nos candidatos a alerta")

    if ml_features.duplicated(
        subset=IDENTIFY_COLUMNS
    ).any():
        raise ValueError("Existem registros duplicados no dataset de features")

#Recuperação de validações anteriores
def load_existing_validation():
    if not OUTPUT_FILE.exists():
        return None

    existing = pd.read_csv(
        OUTPUT_FILE,
        encoding="utf-8-sig"
    )

    required_columns = (
        IDENTIFY_COLUMNS + BUSINESS_VALIDATION_COLUMNS
    )

    missing_columns = [
        column 
        for column in required_columns
        if column not in existing.columns
    ]

    if missing_columns:
        return None

    return existing[required_columns].copy()

#Construção do dataset
def build_validation_dataset(alert_candidates, ml_features):

    currency_context = (
        ml_features[
            IDENTIFY_COLUMNS
            + [
                "currency",
                "currency_type"
            ]
        ]
        .copy()
    )

    result = alert_candidates.merge(
        currency_context,
        on=IDENTIFY_COLUMNS,
        how="left",
        validate="one_to_one"
    )

    invalid_currency_type = (
        result["currency_type"]
        .ne(0)
    )

    if invalid_currency_type.any():
        invalid_values = (
            result.loc[
                invalid_currency_type,
                "currency_type"
            ]
            .drop_duplicates()
            .tolist()
        )

        raise ValueError(
            "Foram encontrados currency_type diferentes de 10 (moeda local): "
            +", ".join(
                str(value)
                for value in invalid_values
            )
        )

    if result[
        "currency"
    ].isna().any():
        raise ValueError("Existem registros sem moeda associada")

    existing_validation = (load_existing_validation())

    if existing_validation is not None:
        result = result.merge(
            existing_validation,
            on=IDENTIFY_COLUMNS,
            how="left",
            validate="one_to_one"
        )
    else:
        for column in (
            BUSINESS_VALIDATION_COLUMNS
        ):
            result[column] = pd.NA

    result["review_status"] = (result["review_status"].fillna("pending"))
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
def print_review_sample(result):
    print("\nTop 1 registros para revisão: \n")

    columns = [
        "anomaly_rank",
        "anomaly_score",
        "materiality_percentile",
        "stability_rate",
        "period_key",
        "company_code",
        "currency_type",
        "currency",
        "reference_document",
        "profit_center",
        "total_absolute_value",
        "dominant_measure",
        "dominant_measure_value",
        "review_status",
        "business_validation"
    ]

    print(result[columns].head(10).to_string(index=False))

#Pipeline principal
def main():

    print("\nKE24 - Dataset de validação de negócio\n")
    if not ALERT_CANDIDATES_FILE.exists():
        raise FileNotFoundError(
            f"Dataset de candidatos não encontrado: {ALERT_CANDIDATES_FILE}"
        )

    if not ML_FEATURES_FILE.exists():
        raise FileNotFoundError(
            f"Dataset de features não encontrado: {ML_FEATURES_FILE}"
        )

    alert_candidates = pd.read_csv(
        ALERT_CANDIDATES_FILE,
        encoding="utf-8-sig"
    )

    ml_features = pd.read_parquet(ML_FEATURES_FILE)
    validate_inputs(alert_candidates, ml_features)
    result = build_validation_dataset(alert_candidates, ml_features)

    result.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print(f"Registros preparados para validação: {len(result):,}")

    pending_count = (result["review_status"].eq("pending").sum())
    print(f"Registros pendentes: {pending_count:,}")

    print_review_sample(result)

    print("\nDataset de validação gerado em: ")
    print(OUTPUT_FILE)

if __name__ == "__main__":
    main()