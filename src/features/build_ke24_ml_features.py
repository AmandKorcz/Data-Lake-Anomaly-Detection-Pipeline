from pathlib import Path
import pandas as pd

#Configurações
PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "ke24_processed.parquet"
)

OUTPUT_DIR = (
    PROJECT_ROOT
    / "data"
    / "curated"
)

OUTPUT_FILE = (
    OUTPUT_DIR
    / "ke24_ml_features.parquet"
)

MANIFEST_FILE = (
    OUTPUT_DIR
    / "ke24_ml_feature_manifest.csv"
)

#Colunas de contexto
CONTEXT_COLUMNS = [
    "_load_id",
    "_source_file_sha256",
    "_source_system",
    "_source_file",
    "_source_row_number",
    "_ingested_at_utc",
    "currency",
    "period",
    "period_year",
    "calendar_year",
    "period_key",
    "period_date",
    "company_code",
    "profit_center",
    "division",
    "product",
    "record_type",
    "reference_document"
]

#Identificação das medidas
def get_measure_columns(df):
    if "sales_quantity" not in df.columns:
        raise ValueError("Coluna sales_quantity não enontrada")

    start_index = df.columns.get_loc(
        "sales_quantity"
    )

    return list(
        df.columns[start_index:]
    )

#Preparação de medidas
def prepare_measure_matrix(df, measure_columns):
    measures = (
        df[measure_columns]
        .apply(
            pd.to_numeric,
            errors="coerce"
        )
        .fillna(0.0)
    )
    return measures

#Seleção das medidas com movimento
def select_active_measures(measures):
    active_columns = [
        column 
        for column in measures.columns
        if measures[column].ne(0).any()
    ]

    inactive_columns = [
        column
        for column in measures.columns
        if not measures[column].ne(0).any()
    ]

    return (
        active_columns,
        inactive_columns
    )

#Feature Engeneering
def build_engineered_features(measures):
    
    positive_measure_count = (
        measures
        .gt(0)
        .sum(axis=1)
    )

    negative_measure_count = (
        measures
        .lt(0)
        .sum(axis=1)
    )

    total_signed_value = (
        measures.sum(axis=1)
    )

    absolute_measures = (
        measures.abs()
    )

    total_absolute_value = (
        absolute_measures.sum(axis = 1)
    )

    max_absolute_value = (
        absolute_measures.max(axis=1)
    )

    engineered = pd.DataFrame({
        "positive_measure_count": positive_measure_count,
        "negative_measure_count": negative_measure_count,
        "total_signed_value": total_signed_value,
        "total_absolute_value": total_absolute_value,
        "max_absolute_value": max_absolute_value,
    })

    return engineered

#Manifesto das features
def build_feature_manifest(measure_columns, active_measure, engineered_columns):
    rows = []

    active_set = set(active_measure)
    for column in measure_columns:

        selected = (column in active_set)

        rows.append({
            "feature": column,
            "feature_type": "financial_measure",
            "selected_for_model": selected,
            "reason": (
                "movement_detected"
                if selected
                else "zero_in_current_sample"
            ),
        })

    for column in engineered_columns:

        rows.append({
            "feature": column,
            "feature_type": "engineered",
            "selected_for_model": True,
            "reason": "derived_financial_behavior",
        })

    return pd.DataFrame(rows)

#Validação
def validate_output(source_df, output_df, engineered_columns):

    if len(source_df) != len(output_df):
        raise ValueError("A quantidade de registros foi alterada durante a criação das features.")

    if output_df.columns.duplicated().any():
        raise ValueError("Existem colunas duplicadas no dataset de ML.")

    if output_df[engineered_columns].isna().any().any():
        raise ValueError("Existem valores nulos nas features derivadas.")

#Pipeline principal
def main():
    print("\nKE24 - Construção de features para ML\n")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Arquivo processed não encontrado {INPUT_FILE}"
        )

    df = pd.read_parquet(INPUT_FILE)

    print(
        f"Registros recebidos: "
        f"{len(df):,}"
    )

    missing_context = [
        column
        for column in CONTEXT_COLUMNS
        if column not in df.columns
    ]

    if missing_context:
        raise ValueError(
            "Colunas de contexto ausentes: "
            +", ".join(missing_context)
        )

    measure_columns = (get_measure_columns(df))

    measures = (prepare_measure_matrix(df, measure_columns))

    (
        active_measures,
        inactive_measures
    ) = select_active_measures(
        measures
    )

    engineered_features = (build_engineered_features(measures))

    model_measures = (
        measures[active_measures].copy()
    )

    ml_dataset = pd.concat(
        [
            df[CONTEXT_COLUMNS].reset_index(drop=True),
            model_measures.reset_index(drop=True),
            engineered_features.reset_index(drop=True)
        ],
        axis=1
    )

    engineered_columns = list(engineered_features.columns)

    validate_output(df, ml_dataset, engineered_columns)
    feature_manifest = (
        build_feature_manifest(
            measure_columns,
            active_measures,
            engineered_columns
        )
    )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    ml_dataset.to_parquet(
        OUTPUT_FILE,
        index=False,
    )

    feature_manifest.to_csv(
        MANIFEST_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print(
        f"\nMedidas financeiras originais: "
        f"{len(measure_columns):,}"
    )

    print(
        f"Medidas selecionadas para ML: "
        f"{len(active_measures):,}"
    )

    print(
        f"Medidas sem movimento na amostra: "
        f"{len(inactive_measures):,}"
    )

    print(
        f"Features derivadas: "
        f"{len(engineered_columns):,}"
    )

    print(
        f"Colunas no dataset de ML: "
        f"{len(ml_dataset.columns):,}"
    )

    print(
        f"Registros no dataser de ML: "
        f"{len(ml_dataset):,}"
    )

    print("\nDataset de ML gerado em: ")
    print(OUTPUT_FILE)

    print("\nManifesto de features gerado em: ")
    print(MANIFEST_FILE)

if __name__ == "__main__":
    main()