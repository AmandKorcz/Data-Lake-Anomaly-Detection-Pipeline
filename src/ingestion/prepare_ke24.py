from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import unicodedata
import uuid

import pandas as pd

from src.privacy.anonymize_ke24 import anonymize_ke24_dataframe
from src.privacy.privacy_runtime import load_privacy_runtime


# -------------------------------- Configurações --------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
OUTPUT_DIR = PROJECT_ROOT / "data" / "staging"
CONSOLIDATED_OUTPUT_FILE = OUTPUT_DIR / "ke24_staging.parquet"
COLUMN_MAPPING_FILE = OUTPUT_DIR / "ke24_column_mapping.csv"


# ------------------------------- Funções auxiliares ----------------------------

def calculate_file_hash(source):
    #SHA-256 dos bytes originais, independentemente da origem do XLSX.
    #Para buffers, restaura a posição de leitura após calcular o hash.

    sha256 = hashlib.sha256()

    if isinstance(source, (str, Path)):
        with open(source, "rb") as file:
            for block in iter(lambda: file.read(65536), b""):
                sha256.update(block)
    else:
        original_position = source.tell()
        try:
            source.seek(0)
            for block in iter(lambda: source.read(65536), b""):
                sha256.update(block)
        finally:
            source.seek(original_position)

    return sha256.hexdigest()


def normalize_column_name(column_name):
    #Normaliza nomes de colunas da extração para uso técnico
    name = str(column_name).strip()
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^A-Za-z0-9]+", "_", name)
    name = name.strip("_").lower()

    if name and name[0].isdigit():
        name = f"c_{name}"
    if not name:
        name = "column"

    return name


def make_unique(column_names):
    #Diferencia nomes repetidos após a normalização
    counters = {}
    unique_names = []

    for name in column_names:
        if name not in counters:
            counters[name] = 1
            unique_names.append(name)
        else:
            counters[name] += 1
            unique_names.append(f"{name}_{counters[name]}")

    return unique_names


def remove_summary_rows(df, input_file_name):
    #Remove linhas de totais somente se estiverem no final da extração
    required_columns = ["period", "period_year"]
    missing_columns = [
        column for column in required_columns if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Não foi possível identificar as linhas de total do arquivo "
            f"'{input_file_name}'. Colunas necessárias ausentes: "
            + ", ".join(missing_columns)
        )

    period_blank = (
        df["period"].isna()
        | df["period"].astype("string").str.strip().eq("").fillna(False)
    )
    period_year_blank = (
        df["period_year"].isna()
        | df["period_year"].astype("string").str.strip().eq("").fillna(False)
    )

    summary_mask = period_blank & period_year_blank
    summary_indices = df.index[summary_mask].tolist()

    if not summary_indices:
        return df, 0

    first_summary_index = summary_indices[0]
    expected_tail_indices = list(range(first_summary_index, len(df)))

    if summary_indices != expected_tail_indices:
        raise ValueError(
            f"O arquivo '{input_file_name}' possui registros sem período "
            "no meio da base. Esses registros não serão removidos automaticamente."
        )

    removed_count = len(summary_indices)
    df = df.loc[~summary_mask].copy()
    return df, removed_count


def standardize_ke24_types(df):
    #Padroniza dimensões; mantém as medidas para a validação financeira
    if "sales_quantity" not in df.columns:
        raise ValueError(
            "Coluna 'sales_quantity' não encontrada. "
            "Não foi possível identificar o início das medidas financeiras."
        )

    measure_start_index = df.columns.get_loc("sales_quantity")
    dimension_columns = list(df.columns[:measure_start_index])
    integer_dimension_columns = {"period"}

    for column in dimension_columns:
        if column in integer_dimension_columns:
            numeric_values = pd.to_numeric(df[column], errors="coerce")
            invalid_values = df[column].notna() & numeric_values.isna()

            if invalid_values.any():
                raise ValueError(
                    f"A coluna '{column}' possui valores que não "
                    "podem ser convertidos para número."
                )

            decimal_values = numeric_values.dropna().mod(1).ne(0)
            if decimal_values.any():
                raise ValueError(
                    f"A coluna '{column}' possui valores decimais "
                    "onde eram esperados valores inteiros."
                )

            df[column] = numeric_values.astype("Int64")
        else:
            df[column] = df[column].astype("string").str.strip()

    return df


# --------------------------- Processamento de uma extração ----------------------

def process_ke24_file(input_file, expected_columns=None, *, source_name=None):
    #Lê e normaliza uma extração XLSX local ou em memória.

    #O DataFrame retornado ainda contém identificadores originais
    #Retorna: (df, mapping, normalized_columns, source_row_count).
    
    if isinstance(input_file, (str, Path)):
        source_name = source_name or Path(input_file).name
    elif not source_name:
        raise ValueError("Informe source_name para arquivos em memória.")

    print("\nProcessando extração SAP KE24\n")

    load_id = str(uuid.uuid4())
    ingestion_timestamp = datetime.now(timezone.utc)
    source_hash = calculate_file_hash(input_file)
    print("Identificação e rastreabilidade da carga geradas.")

    # Primeira leitura: identificação dos cabeçalhos.
    if hasattr(input_file, "seek"):
        input_file.seek(0)
    header_df = pd.read_excel(input_file, nrows=0)
    source_columns = list(header_df.columns)

    normalized_headers = make_unique(
        [normalize_column_name(column) for column in source_columns]
    )

    if "sales_quantity" not in normalized_headers:
        raise ValueError("Coluna sales_quantity não encontrada no Excel.")

    measure_start_index = normalized_headers.index("sales_quantity")
    text_columns = {
        source_column: "string"
        for source_column, technical_column in zip(
            source_columns[:measure_start_index],
            normalized_headers[:measure_start_index],
        )
        if technical_column != "period"
    }

    # Segunda leitura: retorna ao INÍCIO antes de chamar read_excel.
    if hasattr(input_file, "seek"):
        input_file.seek(0)
    df = pd.read_excel(input_file, dtype=text_columns)

    if df.empty:
        raise ValueError(f"O arquivo '{source_name}' está vazio")

    source_row_count = len(df)
    source_column_count = len(df.columns)
    print(f"Linhas encontradas: {source_row_count:,}")
    print(f"Colunas encontradas: {source_column_count:,}")

    original_columns = list(df.columns)
    normalized_columns = make_unique(
        [normalize_column_name(column) for column in original_columns]
    )

    # Garante o mesmo contrato e a mesma ORDEM de colunas.
    if expected_columns is not None:
        expected_columns = list(expected_columns)
        missing_columns = [
            column for column in expected_columns if column not in normalized_columns
        ]
        unexpected_columns = [
            column for column in normalized_columns if column not in expected_columns
        ]

        if missing_columns or unexpected_columns:
            error_message = (
                f"O arquivo '{source_name}' possui um layout "
                "diferente dos demais arquivos."
            )
            if missing_columns:
                error_message += "\nColunas ausentes: " + ", ".join(missing_columns)
            if unexpected_columns:
                error_message += (
                    "\nColunas inesperadas: " + ", ".join(unexpected_columns)
                )
            raise ValueError(error_message)

        if normalized_columns != expected_columns:
            raise ValueError(
                f"O arquivo '{source_name}' possui layout com "
                "ordem das colunas KE24 divergente do contrato esperado."
            )

    mapping = pd.DataFrame(
        {
            "source_file": [source_name] * len(original_columns),
            "source_column": original_columns,
            "technical_column": normalized_columns,
        }
    )

    df.columns = normalized_columns
    df, summary_rows_removed = remove_summary_rows(df, source_name)
    print(f"Linhas de totais removidas: {summary_rows_removed}")

    df = standardize_ke24_types(df)

    # Preserva os seis metadados técnicos usados pelo pipeline.
    metadata = pd.DataFrame(
        {
            "_load_id": [load_id] * len(df),
            "_source_file_sha256": [source_hash] * len(df),
            "_source_system": ["SAP_KE24"] * len(df),
            "_source_file": [source_name] * len(df),
            "_source_row_number": range(2, len(df) + 2),
            "_ingested_at_utc": [ingestion_timestamp] * len(df),
        }
    )

    df = pd.concat(
        [metadata.reset_index(drop=True), df.reset_index(drop=True)],
        axis=1,
    )

    if df.columns.duplicated().any():
        raise ValueError(
            f"O arquivo '{source_name}' gerou nomes de colunas duplicados."
        )

    return df, mapping, normalized_columns, source_row_count


# ---------------------------- Pipeline local legado -----------------------------

def main():
    #Executa a ingestão local, sempre protegendo antes de gravar Parquet
    privacy_policy, privacy_schema, secret_key = load_privacy_runtime()

    print("\nKE24 - Pipeline de Ingestão\n")

    if not RAW_DIR.exists():
        raise FileNotFoundError(f"Pasta RAW não encontrada: {RAW_DIR}")

    input_files = sorted(
        file
        for file in RAW_DIR.iterdir()
        if file.is_file()
        and file.suffix.lower() == ".xlsx"
        and not file.name.startswith("~$")
    )

    if not input_files:
        raise FileNotFoundError(f"Nenhum arquivo .xlsx encontrado em {RAW_DIR}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Arquivos encontrados: {len(input_files)}")
    print("Nomes dos arquivos de origem omitidos por segurança.")

    dataframes = []
    mappings = []
    expected_columns = None
    total_source_rows = 0

    for file_number, input_file in enumerate(input_files, start=1):
        try:
            df, mapping, normalized_columns, source_row_count = process_ke24_file(
                input_file,
                expected_columns=expected_columns,
            )

            # O resultado de process_ke24_file nunca é gravado sem proteção.
            df = anonymize_ke24_dataframe(
                df=df,
                policy=privacy_policy,
                schema=privacy_schema,
                secret_key=secret_key,
            )
        except Exception:
            # Não expõe, no terminal, conteúdos sensíveis da extração.
            raise RuntimeError(
                f"Falha no processamento da extração "
                f"{file_number}/{len(input_files)}. "
                "Verifique a origem em ambiente restrito."
            ) from None

        if df.empty:
            raise ValueError("A extração não possui registros após o tratamento.")

        # Substitui o nome original por seu identificador protegido no mapeamento.
        protected_file_id = str(df["_source_file"].iloc[0])
        mapping["source_file"] = protected_file_id

        if expected_columns is None:
            expected_columns = normalized_columns

        total_source_rows += source_row_count
        dataframes.append(df)
        mappings.append(mapping)

        # Mantém a geração dos Parquets locais protegidos já existente.
        individual_output_file = (
            OUTPUT_DIR / f"ke24_{protected_file_id}_staging.parquet"
        )
        df.to_parquet(individual_output_file, index=False)
        print(f"Parquet individual: {individual_output_file.name}")

    consolidated_df = pd.concat(dataframes, ignore_index=True)
    consolidated_df.to_parquet(CONSOLIDATED_OUTPUT_FILE, index=False)

    consolidated_mappings = pd.concat(mappings, ignore_index=True)
    consolidated_mappings.to_csv(
        COLUMN_MAPPING_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print("\nIngestão concluída com sucesso!\n")
    print(f"Arquivos processados: {len(input_files)}")
    print(f"Registros de origem: {total_source_rows:,}")
    print(f"Registros consolidados: {len(consolidated_df):,}")
    print(f"Cargas identificadas: {consolidated_df['_load_id'].nunique()}")
    print(f"Empresas identificadas: {consolidated_df['company_code'].nunique()}")
    print(f"Colunas KE24: {len(expected_columns)}")
    print(f"Colunas totais no staging: {len(consolidated_df.columns)}")
    print("\nParquet consolidado:")
    print(CONSOLIDATED_OUTPUT_FILE)
    print("\nMapa de colunas:")
    print(COLUMN_MAPPING_FILE)


if __name__ == "__main__":
    main()
