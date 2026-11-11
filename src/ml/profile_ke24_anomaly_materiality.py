from pathlib import Path

import pandas as pd
import numpy as np

#Configurações
PROJECT_ROOT = Path(__file__).resolve().parents[2]

ML_FEATURES_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_ml_features.parquet"
)

MANIFEST_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_ml_feature_manifest.csv"
)

ANOMALY_RESULTS_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_anomaly_results.parquet"
)

OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_anomaly_materiality.csv"
)

IDENTITY_COLUMNS = [
    "_load_id",
    "_source_file_sha256",
    "_source_file",
    "_source_row_number"
]

CONTEXT_COLUMNS = [
    "period_key",
    "company_code",
    "profit_center",
    "division",
    "product",
    "record_type",
    "reference_document"
]

#Features financeiras
def get_financial_features(manifest):

    selected_mask = (
        manifest["selected_for_model"]
        .astype("string")
        .str.lower()
        .eq("true")
    )

    financial_mask = (
        manifest["feature_type"]
        .eq("financial_measure")
    )

    financial_features = (
        manifest.loc[
            selected_mask & financial_mask,
            "feature"
        ]
        .tolist()
    )

    if not financial_features:
        raise ValueError("Nenhuma medida financeira selecionada foi encontrada.")

    return financial_features

#Validação
def validate_inputs(ml_features, anomaly_results, financial_features):

    required_feature_columns = (
        IDENTITY_COLUMNS
        + ["currency", "currency_type"]
        + financial_features
        + [
            "total_signed_value",
            "total_absolute_value",
            "max_absolute_value",
        ]
    )

    missing_features = [
        column 
        for column in required_feature_columns
        if column not in ml_features.columns
    ]

    if missing_features:
        raise ValueError(
            "Colunas ausentes no dataset ML: "
            +", ".join(missing_features)
        )

    required_feature_columns = (
        IDENTITY_COLUMNS
        + CONTEXT_COLUMNS
        + [
            "anomaly_score",
            "anomaly_flag",
            "anomaly_rank"
        ]
    )

    missing_results = [
        column
        for column in required_feature_columns
        if column not in anomaly_results.columns
    ]

    if missing_results:
        raise ValueError(
            "Colunas ausentes nos resultados: "
            +", ".join(missing_results)
        )

#Construção da análise de materialidade
def build_materiality_dataset(ml_features, anomaly_results, financial_features):
    feature_columns = (
        IDENTITY_COLUMNS
        + ["currency", "currency_type"]
        + financial_features
        + [
            "total_signed_value",
            "total_absolute_value",
            "max_absolute_value",
        ]
    )

    financial_data = (
        ml_features[feature_columns]
        .copy()
    )

    result = anomaly_results.merge(
        financial_data,
        on=IDENTITY_COLUMNS,
        how="left",
        validate="one_to_one"
    )

    measure_matrix = (
        result[financial_features]
        .apply(
            pd.to_numeric,
            errors="coerce"
        )
    )

    if measure_matrix.isna().any().any():
        raise ValueError("Existem valores inválidos nas medidas financeiros.")

    result[
        "active_measure_count_review"
    ] = (
        measure_matrix
        .ne(0)
        .sum(axis=1)
    )

    absolute_matrix = (
        measure_matrix.abs()
    )

    result[
        "dominant_measure"
    ] = (
        absolute_matrix.idxmax(axis=1)
    )

    result[
        "dominant_measure_value"
    ] = [
        measure_matrix.loc[
            index,
            measure
        ]
        for index, measure
        in result[
            "dominant_measure"
        ].items()
    ]

    zero_materiality = (
        result["total_absolute_value"].eq(0)
    )

    result.loc[
        zero_materiality,
        "dominant_measure"
    ] = pd.NA

    result.loc[
        zero_materiality,
        "dominant_measure_value"
    ] = 0.0

    result["materiality_percentile"] = (
        calculate_materiality_percentile(result)
    )

    return result


def calculate_materiality_percentile(dataset):
    required = {
        "company_code",
        "currency",
        "currency_type",
        "total_absolute_value",
    }

    missing = required - set(dataset.columns)

    if missing:
        raise ValueError(
            f"Missing materiality fields: {sorted(missing)}"
        )

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

    amounts = pd.to_numeric(
        dataset["total_absolute_value"],
        errors="coerce",
    )

    if (
        companies.isna().any()
        or companies.eq("").any()
    ):
        raise ValueError("Invalid company_code")

    valid_currencies = (
        currencies.str.fullmatch(r"[A-Z]{3}")
        .fillna(False)
    )

    if not valid_currencies.all():
        raise ValueError("Invalid currency")

    if (
        currency_types.isna().any()
        or not currency_types.eq(10).all()
    ):
        raise ValueError("Expected currency_type 10")

    if (
        amounts.isna().any()
        or not amounts.ge(0).all()
        or not np.isfinite(
            amounts.to_numpy(dtype=float)
        ).all()
    ):
        raise ValueError("Invalid materiality amounts")

    grouped_data = pd.DataFrame({
        "company_code": companies,
        "currency": currencies,
        "amount": amounts,
    }, index=dataset.index)

    return (
        grouped_data
        .groupby(["company_code", "currency"])["amount"]
        .rank(method="average", pct=True)
        .mul(100)
    )

#Perfil da materialidade
def print_materiality_profile(result):

    values = (
        result["total_absolute_value"]
    )

    percentiles = [
        0.50,
        0.75,
        0.90,
        0.95,
        0.99,
        1.00
    ]

    print("\nDistribuição da materialidade financeira: \n")

    for percentile in percentiles:
        value = values.quantile(
            percentile
        )
        label = int(
            percentile * 100
        )

        print(
            f"P{label}: "
            f"{value:,.2f}"
        )

#Exibição dos registros mais anômalos
def print_top_anomalies(result):

    top = (
        result
        .sort_values(
            "anomaly_rank"
        )
        .head(10)
    )

    print("\nTop 10 registros do ranking de anomalias: \n")

    print(
        top[
            [
                "anomaly_rank",
                "anomaly_flag",
                "anomaly_score",
                "period_key",
                "reference_document",
                "profit_center",
                "total_absolute_value",
                "materiality_percentile",
                "dominant_measure",
                "dominant_measure_value"
            ]
        ]
        .to_string(
            index=False
        )
    )

#Pipeline principal
def main():

    print("\nKE24 - Perfil de Materialidade dos Alertas\n")

    required_files = [
        ML_FEATURES_FILE,
        MANIFEST_FILE,
        ANOMALY_RESULTS_FILE
    ]

    for file_path in required_files:
        if not file_path.exists():
            raise FileNotFoundError(
                f"Arquivo não encontrado {file_path}"
            )

    ml_features = pd.read_parquet(ML_FEATURES_FILE)

    manifest = pd.read_csv(
        MANIFEST_FILE,
        encoding="utf-8-sig"
    )

    anomaly_results = pd.read_parquet(ANOMALY_RESULTS_FILE)

    financial_features = (get_financial_features(manifest))

    validate_inputs(ml_features, anomaly_results, financial_features)

    result = (
        build_materiality_dataset(ml_features, anomaly_results, financial_features)
    )

    result.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print(f"Registros analisados: {len(result):,}")
    print(f"Medidas financeiras consideradas: {len(financial_features):,}")

    flagged_count = int(
        result[
            "anomaly_flag"
        ].sum()
    )

    print(f"Registros sinalizados pelo modelo: {flagged_count:,}")
    print_materiality_profile(result)
    print_top_anomalies(result)

    print("\nResultado gerado em:")
    print(OUTPUT_FILE)

if __name__ == "__main__":
    main()