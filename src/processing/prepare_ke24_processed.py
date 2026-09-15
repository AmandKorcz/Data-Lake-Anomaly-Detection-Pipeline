from pathlib import Path 

import pandas as pd

#Configurações
PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "staging"
    / "ke24_staging.parquet"
)

OUTPUT_DIR = (
    PROJECT_ROOT
    / "data"
    / "processed"
)

OUTPUT_FILE = (
    OUTPUT_DIR
    / "ke24_processed.parquet"
)

#Validação do staging
def validate_input_data(df):
    required_columns = [
        "_load_id",
        "_source_file_sha256",
        "_source_system",
        "_source_file",
        "_source_row_number",
        "_ingested_at_utc",
        "currency",
        "period",
        "period_year",
        "company_code",
        "sales_quantity",
    ]

    missing_columns = [
        column 
        for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            "Colunas obrigatórias ausentes no staging: "
            + ", ".join(missing_columns)
        )

    if df.empty:
        raise ValueError("O staging não possui registros")

    if df.columns.duplicated().any():
        raise ValueError("Existem colunas duplicadas no staging")

#Padronização das medidas financeiras
def standardize_measure_types(df):

    measure_start_index = df.columns.get_loc("sales_quantity")

    measure_columns = list(
        df.columns[measure_start_index:]
    )

    for column in measure_columns:
        original_values = df[column]

        numeric_values = pd.to_numeric(
            original_values,
            errors="coerce"
        )

        invalid_values = (
            original_values.notna()
            & original_values
            .astype("string")
            .str.strip()
            .ne("")
            &numeric_values.isna()
        )

        if invalid_values.any():
            raise ValueError(
                f"A medida financeira '{column}' possui valores não numéricos"
            )

        df[column] = numeric_values

    df = df.copy()

    return df, measure_columns

#Criação da chave temporal
def add_period_key(df):
    period_values = pd.to_numeric(
        df["period"],
        errors="coerce"
    )

    if period_values.isna().any():
        raise ValueError("Exstem valores inválidos em period.")

    invalid_period = (
        (period_values < 1) | (period_values > 12) | period_values.mod(1).ne(0)
    )

    if invalid_period.any():
        raise ValueError(
            "A coluna period possui valores fora do intervalo de 1 a 12"
        )

    period_values = period_values.astype("Int64")

    period_year_text = (
        df["period_year"]
        .astype("string")
        .str.strip()
    )

    extracted = period_year_text.str.extract(
        r"^(?P<source_period>\d{1,2})\.(?P<calendar_year>\d{4})$"
    )

    if extracted["calendar_year"].isna().any():
        invalid_examples = (
            period_year_text[extracted["calendar_year"].isna()]
            .drop_duplicates()
            .tolist()
        )

        raise ValueError(
            "Formato inválido em period_year. Esperado período.ano, por exemplo 12.2025. "
            f"Valores encontrados: {invalid_examples}"
        )

    source_period = pd.to_numeric(
        extracted["source_period"],
        errors="raise"
    ).astype("Int64")

    calendar_year = pd.to_numeric(
        extracted["calendar_year"],
        errors="raise"
    ).astype("Int64")

    period_mismatch = (
        source_period != period_values
    )

    if period_mismatch.any():
        raise ValueError(
            "Inconsistência entre period e period_year"
        )

    period_key = (
        calendar_year.astype("string")
        + "-"
        + period_values
        .astype("string")
        .str.zfill(2)
    )

    period_date = pd.to_datetime(
        {
            "year": calendar_year.astype("int64"),
            "month": period_values.astype("int64"),
            "day": 1,
        }
    )

    insert_position = (
        df.columns.get_loc("period_year") + 1
    )

    df.insert(
        insert_position,
        "calendar_year",
        calendar_year
    )

    df.insert(
        insert_position + 1,
        "period_key",
        period_key
    )

    df.insert(
        insert_position + 2,
        "period_date",
        period_date
    )

    return df

#Organização dos registros 
def sort_processed_data(df):
    sort_columns = [
        "calendar_year",
        "period",
        "company_code",
        "_source_file",
        "_source_row_number",
    ]

    df = df.sort_values(
        by=sort_columns,
        kind="stable"
    ).reset_index(drop=True)

    return df

def validate_processed_data(df, source_row_count):
    if len(df) != source_row_count:
        raise ValueError("A quantidade de registros foi alterada durante o processamento")

    if df.columns.duplicated().any():
        raise ValueError("Existem colunas duplicadas na camada processed")

    required_processed_columns = [
        "calendar_year",
        "period_key",
        "period_date"
    ]

    missing_columns = [
        column 
        for column in required_processed_columns
        if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            "Campos processados obrigatórios ausentes: "
            +", ".join(missing_columns)
        )

    if df["period_date"].isna().any():
        raise ValueError("Existem períodos sem period_date")

    if df["period_key"].isna().any():
        raise ValueError("Existem períodos sem period_key")

#Pipeline principal 
def main():
    print("\nKE24 - Camada Processed\n")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Arquivo staging não encontrado: {INPUT_FILE}"
        )

    print(f"Arquivo de entrada: {INPUT_FILE.name}")

    df = pd.read_parquet(INPUT_FILE)

    source_row_count = len(df)
    source_column_count = len(df.columns)

    print(
        f"Registros recebidos: "
        f"{source_row_count:,}"
    )

    print(
        f"Colunas recebidas: "
        f"{source_column_count:,}"
    )

    validate_input_data(df)

    df, measure_columns = (
        standardize_measure_types(df)
    )

    df = add_period_key(df)
    df = sort_processed_data(df)

    validate_processed_data(
        df,
        source_row_count
    )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    df.to_parquet(
        OUTPUT_FILE,
        index=False
    )

    print(
        f"\nMedidas financeiras identificadas: "
        f"{len(measure_columns):,}"
    )

    print(
        f"Registros processados: "
        f"{len(df):,}"
    )

    print(
        f"Colunas processadas: "
        f"{len(df.columns):,}"
    )

    print("\nCamada processed gerada com sucesso!")

    print(
        f"\nArquivo gerado: "
        f"{OUTPUT_FILE}"
    )

if __name__ == "__main__":
    main()