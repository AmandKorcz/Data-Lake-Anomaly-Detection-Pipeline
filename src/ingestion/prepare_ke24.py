from pathlib import Path
from datetime import datetime, timezone
import hashlib
import re
import unicodedata
import uuid


import pandas as pd

# ------------------------------------ Configurações do projeto ------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]

RAW_DIR = (
    PROJECT_ROOT
    / "data"
    / "raw"
)

OUTPUT_DIR = (
    PROJECT_ROOT
    / "data"
    / "staging"
)

CONSOLIDATED_OUTPUT_FILE = (
    OUTPUT_DIR
    / "ke24_staging.parquet"
)

COLUMN_MAPPING_FILE = (
    OUTPUT_DIR
    / "ke24_column_mapping.csv"
)

# ------------------------------------ Funções Auxiliares ------------------------------------
def calculate_file_hash(file_path):
    #Calcula o hash do arquivo de origem, permitindo a identificação de exatamente qual arquivo foi utilizado em determinada carga

    sha256 = hashlib.sha256()

    with open(file_path, "rb") as file:
        for block in iter(lambda: file.read(65536), b""):
            sha256.update(block)

    return sha256.hexdigest()

def normalize_column_name(column_name):
    # Converte os nomes das colunas da base para compatibilidade com Python e BigQuery.

    name = str(column_name).strip()

    # Remove acentuação.
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")

    # Substitui espaços e caracteres especiais por underline.
    name = re.sub(r"[^A-Za-z0-9]+", "_", name)

    # Remove underline no início/fim e utiliza letras minúsculas.
    name = name.strip("_").lower()

    # Evita nomes iniciados por números.
    if name and name[0].isdigit():
        name = f"c_{name}"

    if not name:
        name = "column"

    return name

def make_unique(column_names):
    #Garantindo que não existam nomes duplicados após a normalização das colunas

    counters = {}
    unique_names = []

    for name in column_names:
        if name not in counters:
            counters[name] = 1
            unique_names.append(name)
        else:
            counters[name] += 1
            unique_names.append(
                f"{name}_{counters[name]}"
            )
    return unique_names

def process_ke24_file(input_file, expected_columns=None):
    print("\n")
    print(f"Processando arquivo: {input_file.name}")
    print("\n")

    #Identifiaação individual da carga
    load_id = str(uuid.uuid4())
    ingestion_timestamp = datetime.now(timezone.utc)
    source_hash = calculate_file_hash(input_file)

    print(f"LOAD ID: {load_id}")
    print(f"SHA-256: {source_hash}")

    #Leitura do Excel 
    df = pd.read_excel(input_file)

    if df.empty:
        raise ValueError(
            f"O arquivo '{input_file.name} está vazio"
        )

    source_row_count = len(df)
    source_column_count = len(df.columns)

    print(f"Linhas encontradoas: {source_row_count:,}")
    print(f"Colunas encontradas: {source_column_count:,}")

    original_columns = list(df.columns)

    normalized_columns = [
        normalize_column_name(column)
        for column in original_columns
    ]

    normalized_columns = make_unique(
        normalized_columns
    )

    if expected_columns is not None:

        missing_columns = [
            column
            for column in expected_columns
            if column not in normalized_columns
        ]

        unexpected_columns = [
            column 
            for column in normalized_columns
            if column not in expected_columns
        ]

        if missing_columns or unexpected_columns:
            error_message = (
                f"O arquivo '{input_file.name}' possui um layout diferente dos demais arquivos"
            )

            if missing_columns:
                error_message += (
                    "\n Colunas ausentes: "
                    + ", ".join(missing_columns)
                )

            if unexpected_columns:
                error_message += (
                    "\nColunas inesperadas: "
                    + ", ".join(unexpected_columns)
                )

            raise ValueError(error_message)

    #Mapeamento original -> técnico
    mapping = pd.DataFrame({
        "source_file": input_file.name * len(original_columns),
        "source_column": original_columns,
        "technical_column": normalized_columns
    })

    df.columns = normalized_columns

    #Masterdados de rastreabilidade
    metadata = pd.DataFrame({
        "_load_id": [load_id] * len(df),
        "_source_file_sha256": [source_hash] * len(df),
        "_source_system": ["SAP_KE24"] * len(df),
        "_source_file": [input_file.name] * len(df),
        "_source_row_number": range(2, len(df) + 2),
        "_ingested_at_utc": [ingestion_timestamp] * len(df)
    })

    df = pd.concat(
        [
            metadata.reset_index(drop=True),
            df.reset_index(drop=True)
        ],
        axis=1
    )

    # Validação técnica
    if df.columns.duplicated().any():
        raise ValueError(
            f"O arquivo '{input_file.name}' gerou "
            "nomes de colunas duplicados."
        )

    return (
        df,
        mapping,
        normalized_columns,
        source_row_count
    )


# ------------------------------------ Pipeline de Ingestão ------------------------------------

def main():

    print(" ")
    print("KE24 - Pipeline de Ingestão")
    print(" ")

    #Vaidação da pasta raw
    if not RAW_DIR.exists():
        raise FileNotFoundError(
            f"Pasta RAW não encontrada: {RAW_DIR}"
        )

    #Localiza os arquivos de excel e ignora os arquivos temporários
    input_files = sorted([
        file
        for file in RAW_DIR.iterdir()
        if file.is_file()
        and file.suffix.lower() == ".xlsx"
        and not file.name.startswith("~$")
    ])

    if not input_files:
        raise FileNotFoundError(
            f"Nenhum arquivo .xlsx encontrado em {RAW_DIR}"
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    print(
        f"\nArquivos encontrados: {len(input_files)}"
    )

    for file in input_files:
        print(f"- {file.name}")

    dataframes = []
    mappings = []

    expected_columns = None
    total_source_rows = 0

    #Processando cada extração individualmente
    for input_file in input_files:

        (
            df, 
            mapping,
            normalized_columns,
            source_row_count
        ) = process_ke24_file(
            input_file,
            expected_columns
        )

    #O primeiro arquivo define o layout esperado da execução
        if expected_columns is None:
            expected_columns = normalized_columns

            total_source_rows += source_row_count

            dataframes.append(df)
            mappings.append(mapping)

            #Gera um .parquet para carga
            individual_output_file = (
                OUTPUT_DIR
                / f"{input_file.stem}_staging.parquet"
            )

            df.to_parquet(
                individual_output_file,
                index=False
            )

            print(
                f"Parquet individual: "
                f"{individual_output_file.name}"
            )

    #Consolida todas as cargas
    consolidated_df = pd.concat(
        dataframes,
        ignore_index=True
    )

    consolidated_df.to_parquet(
        CONSOLIDATED_OUTPUT_FILE,
        index=False
    )

    #Consolidando os mapeamentos de colunas 
    consolidated_mappings = pd.concat(
        mappings,
        ignore_index=True
    )

    consolidated_mappings.to_csv(
        COLUMN_MAPPING_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    #Resultado
    print()
    print("\nIngestão concluída com sucesso!\n")

    print(
        f"\nArquivos processados: "
        f"{len(input_files)}"
    )

    print(
        f"Registros de origem: "
        f"{total_source_rows:,}"
    )

    print(
        f"Registros consolidados: "
        f"{len(consolidated_df):,}"
    )

    print(
        f"Cargas identificadas: "
        f"{consolidated_df['_load_id'].nunique()}"
    )

    print(
        f"Empresas identificadas: "
        f"{consolidated_df['company_code'].nunique()}"
    )

    print(
        f"Colunas KE24: "
        f"{len(expected_columns)}"
    )

    print(
        f"Colunas totais no staging: "
        f"{len(consolidated_df.columns)}"
    )

    print("\nParquet consolidado: ")
    print(CONSOLIDATED_OUTPUT_FILE)

    print("\nMapa de colunas:")
    print(COLUMN_MAPPING_FILE)

if __name__ == "__main__":
    main()