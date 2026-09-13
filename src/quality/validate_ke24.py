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

QUALITY_RULES_FILE = (
    PROJECT_ROOT
    / "config"
    / "ke24_quality_rules.json"
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

def load_quality_rules():
    if not QUALITY_RULES_FILE.exists():
        raise FileNotFoundError(
            f"Regras de qualidade para validação não encontradas: "
            f"{QUALITY_RULES_FILE}"
        )

    with open(
        QUALITY_RULES_FILE,
        "r",
        encoding="utf-8"
    ) as file:
        return json.load(file)

#Validaçaõ de campos essenciais
def validate_required_fields(df, rules):

    errors = []

    for column in rules["required_not_null"]:
        #Verifica se a coluna existe
        if column not in df.columns:
            errors.append(
                f"Campos obrigatórios não encontrados: "
                f"'{column}'"
            )
            continue

        #Verifica valores nulos
        invalid_values = df[column].isna()

        #Também considera texto vazio como inválido
        if pd.api.types.is_string_dtype(df[column].dtype):
            invalid_values = (
                invalid_values
                | df[column].astype("string").str.strip().eq("")
            )

        invalid_count = invalid_values.sum()

        if invalid_count > 0:
            errors.append(
                f"O campo obrigatório '{column}' possui {invalid_count} valores vazios ou nulos"
            )

    return errors

#Vaidaçaõ de campos semanticamente inteiros
def validate_integer_like_columns(df, rules):

    errors = []

    for column in rules["integer_like_columns"]:

        if column not in df.columns:
            continue

        numeric_values = pd.to_numeric(
            df[column],
            errors="coerce"
        )

        invalid_numeric = (
            df[column].notna()
            & numeric_values.isna()
        )

        if invalid_numeric.any():
            errors.append(
                f"O campo '{column}' possui valores não numéricos"
            )
            continue

        decimal_values = (
            numeric_values
            .dropna()
            .mod(1)
            .ne(0)
        )

        if decimal_values.any():
            errors.append(
                f"O campo '{column}' possui valores decimais onde eram esperados inteiros."
            )

    return errors

#Validação de medidas financeiras
def validate_numeric_measures(df, schema, rules):

    errors = []
    schema_columns = schema["columns"]
    start_column = rules["numeric_measure_start_column"]

    if start_column not in schema_columns:
        errors.append(
            f"Coluna inicial das medidas financeiras não encontrada no schema: "
            f"'{start_column}'"
        )

        return errors

    start_index = schema_columns.index(
        start_column
    )

    measure_columns = schema_columns[
        start_index:
    ]

    invalid_columns = []

    for column in measure_columns:
        if column not in df.columns:
            continue

        converted_values = pd.to_numeric(
            df[column],
            errors="coerce"
        )

        invalid_values = (
            df[column].notna()
            & converted_values.isna()
        )

        if invalid_columns:
            errors.append(
                "Medidas financeiras com valores não numéricos: "
                + ", ".join(invalid_columns)
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
    quality_rules = load_quality_rules()

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

    errors.extend(
        validate_required_fields(df, quality_rules)
    )

    errors.extend(
        validate_integer_like_columns(df, quality_rules)
    )

    errors.extend(
        validate_numeric_measures(df, schema, quality_rules)
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
        f"{schema['expected_column_count']} colunas KE24"
    )
    print(
        f"Metadados validados: "
        f"{len(METADATA_COLUMNS)} campos técnicos"
    )
    print(
        f"Regras de qualidade: "
        f"{quality_rules['rules_version']}"
    )
    print("Campos essenciais e medidas financeiras validadas")
    print("Nenhuma inconsistência estrutural identificada")

if __name__ == "__main__":
    main()