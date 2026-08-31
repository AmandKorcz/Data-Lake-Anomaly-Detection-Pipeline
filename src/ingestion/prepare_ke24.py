from pathlib import Path
from datetime import datetime, timezone
import hashlib
import re
import unicodedata
import uuid


import pandas as pd

# ------------------------------------ Configurações do projeto ------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "database_KE24_besco.xlsx"
)

OUTPUT_DIR = PROJECT_ROOT / "data" / "staging"

OUTPUT_FILE = OUTPUT_DIR / "ke24_staging.parquet"

COLUMN_MAPPING_FILE = OUTPUT_DIR / "ke24_column_mapping.csv"

# ------------------------------------ Funções Auxiliares ------------------------------------
def calculate_file_hash(file_path):
    #Calcula o hash do arquivo de origem, permitindo a identificação de exatamente qual arquivo foi utilizado em determinada carga

    sha256 = hashlib.sha256()

    with open(file_path, "rb") as file:
        for block in iter(lambda: file.read(65536), b""):
            sha256.update(block)

    return sha256.hexdigest()

def normalize_column_name(column_name):
    #Convertendo os nomes das colunas da base de dados para compatibilidade com Python e BigQuery

    name = str(column_name).strip()

    #Removendo acentuação
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")

    #Substituindo espaços e caracteres especiais por underline
    name = re.sub(r"^A-Za-z0-9]+", "_", name)

    #Removendo underline no início/fim e transformando em letras minusculas
    name = name.strip("_").lower()

    #Evitando nomes iniciados por números
    if name and name[0].isdigit():
        name = f"c_{name}"

    if not name:
        nome = "column"

    return

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


# ------------------------------------ Pipeline de Ingestão ------------------------------------

def main():

    print(" ")
    print("KE24 - Pipeline de Ingestão")
    print(" ")

    #Vaidação do arquivo
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Arquivo não encontrado: {INPUT_FILE}"
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    print(f"\nArquivo de origem: {INPUT_FILE.name}")

    #Identificação da carga
    load_id = str(uuid.uuid4())

    ingestion_timestamp = datetime.now(timezone.utc)

    source_hash = calculate_file_hash(INPUT_FILE)

    print(f"LOAD ID: {load_id}")
    print(f"SHA-256: {source_hash}")

    #Leitura da KE24
    print("\nLendo a base de dados...")

    df = pd.read_excel(INPUT_FILE)

    if df.empty:
        raise ValueError(
            "A base de dados está vaziia"
        )

    source_row_count = len(df)
    source_column_count = len(df.columns)

    print(f"Linhas encontradas: {source_row_count:,}")
    print(f"Colunas encontradas: {source_column_count:,}")

    #Preserva e normaliza as colunas
    original_columns = list(df.columns)

    normalized_columns = [
        normalize_column_name(column)
        for column in original_columns
    ]

    normalized_columns = make_unique(
        normalized_columns
    )

    mapping = pd.DataFrame({
        "source_column": original_columns,
        "technical_column": normalized_columns
    })

    mapping.to_csv(
        COLUMN_MAPPING_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    df.columns = normalized_columns

    #Metadados de rastreabilidade
    metadata = pd.DataFrame({
        "_load_id": [load_id] * len(df),
        "_source_file_sha256": [source_hash] * len(df),
        "_source_system": ["SAP_KE24"] * len(df),
        "_source_file": [INPUT_FILE.name] * len(df),
        "_source_row_number": range(2, len(df) + 2),
        "_ingested_at_utc": [ingestion_timestamp] * len(df)
    })

    df = pd.concat(
        [
            metadata.reset_index(drop=True),
            df.reset_index(drop=True)
        ],
        axis = 1
    )

    #Validações técnicas
    if df.columns.duplicated().any():
        raise ValueError(
            "Existem nomes de colunas duplicados"
        )

    #Esse é apenas um aviso, registros com dimensões semelhantes podem representar movimetações financeiras diferentes, por isso não farei nenhuma remoção

    #Geração do Parquet
    df.to_parquet(
        OUTPUT_FILE,
        index=False
    )

    #Resultado
    print("\n")
    print("Ingestão Concluída com Sucesso!")
    print(" ")

    print(f"\nRegistros de origem: {source_row_count:,}")
    print(f"Registros no staging: {len(df):,}")

    print(f"Colunas de origem: {source_column_count:,}")

    print(f"Colunas no staging: {len(df.columns):,}")

    print(f"\nParquet:")
    print(OUTPUT_FILE)

    print(f"\nMapa de Colunas:")
    print(COLUMN_MAPPING_FILE)



if __name__ == "__main__":
    main()