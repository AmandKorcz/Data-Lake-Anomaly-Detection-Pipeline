from pathlib import Path
import json
import sys

import pandas as pd

# ----------------------- Configurações -------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "staging"
    / "ke24_staging.parquet"
)

SCHEMA_FILE = (
    PROJECT_ROOT
    / "config"
    / "ke24_schema.json"
)

METADATA_COLUMNS = [
    "_load_id",
    "_source_file_sha256",
    "_source_system",
    "_source_file",
    "_source_row_number",
    "_ingested_at_utc",
]

# ----------------------- Funções Auxiliares -------------------------------
def load_schema():
    if not SCHEMA_FILE.exists():
        raise FileNotFoundError(
            f"Contrato de schema não encontrado: {SCHEMA_FILE}"
        )
    with open(SCHEMA_FILE, "r", encoding="utf-8") as file:
        return json.load(file)

def validate_schema(df, schema):
    errors = []

    expected_columns = schema["columns"]
    expected_columns_count = schema["expected_column_count"]

    ke24_columns = [
        column
        for column in df.columns
        if column not in METADATA_COLUMNS
    ]

    missing_columns = [
        column
        for column in expected_columns
        if column not in ke24_columns
    ]

    unexpected_columns = [
        column
        for column in ke24_columns
        if column not in expected_columns
    ]

    if len(ke24_columns) != expected_columns_count:
        errors.append(
            "Quantidade de colunas diferente do contrato"
            f"Esperado: {expected_columns_count} | "
            f"Encontrado: {len(ke24_columns)}"
        )

    if missing_columns:
        errors.append(
            "Colunas esperadas não encontradas: "
            + ", ".join(missing_columns)
        )

    if unexpected_columns:
        errors.append(
            "Colunas inesperadas encontradas: "
            + ", ".join(unexpected_columns)
        )

    return errors

def validate_metadata(df):
    errors = []

    missing_metadata = [
        column 
        for column in METADATA_COLUMNS
        if column not in df.columns
    ]

    if missing_metadata:
        errors.append(
            "Metadadados obrigatórios não encontrados: "
            + ", ".join(missing_metadata)
        )

        return errors

    for column in METADATA_COLUMNS:
        if df[column].isna().any():
            errors.append(
                f"O metadado '{column}' possui valores nulos."
            )

    duplicated_source_rows = df.duplicated(
        subset=[
            "_load_id",
            "_source_row_number"
        ]
    ).sum()

    if duplicated_source_rows > 0:
        errors.append(
            "Foram encontradas "
            f"{duplicated_source_rows} linhas com identificação de origem duplicada"
        )

    return errors

# ----------------------- Validação principal -------------------------------
def main():

    print("\nKE24 - Validação de qualidade\n")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Arquivo staging não encontrado: {INPUT_FILE}"
        )

    print(f"Arquivo analisado: {INPUT_FILE.name}")

    df = pd.read_parquet(INPUT_FILE)

    if df.empty:
        print("\nStatus: REPROVADO")
        print("A base não possui registros.")
        sys.exit(1)

    schema = load_schema()

    print(f"Contrato: {schema['schema_name']}")
    print(f"Versão do schema: {schema['schema_version']}")
    print(f"Registros analisados: {len(df):,}")

    errors = []

    errors.extend(
        validate_schema(df, schema)
    )

    errors.extend(
        validate_metadata(df)
    )

    print()

    if errors:
        print("Status: REPROVADO\n")
        for index, error in enumerate(errors, start=1):
            print(f"{index}. {error}")

        sys.exit(1)

    print("Status: APROVADO\n")
    print(
        f"Schema validado: "
        f"{len(METADATA_COLUMNS)} campos técnicos"
    )
    print("Nenhuma inconsistência estrutural identificada")

if __name__ == "__main__":
    main()