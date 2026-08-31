from pathlib import Path
import json

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "staging"
    / "ke24_staging.parquet"
)

OUTPUT_DIR = PROJECT_ROOT / "config"

OUTPUT_FILE = OUTPUT_DIR / "ke24_schema.json"

METADATA_COLUMNS = [
    "_load_id",
    "_source_file_sha256",
    "_source_system",
    "_source_file",
    "_source_row_number",
    "_ingested_at_utc",
]

def main():
    print("\nKE24 - Criação do contrato de schema\n")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Arquivo stating não encontrado: {INPUT_FILE}"
        )

    print(f"\nLendo arquivo: {INPUT_FILE.name}")

    df = pd.read_parquet(INPUT_FILE)    

    #Removendo somente os metadados adicionados pelo próprio pipeline
    ke24_columns = [
        column
        for column in df.columns
        if column not in METADATA_COLUMNS
    ]

    schema_contract = {
        "schema_name": "SAP_KE24",
        "schema_version": "1.0",
        "expected_column_count": len(ke24_columns),
        "columns": ke24_columns,
    }

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            schema_contract,
            file,
            ensure_ascii=False,
            indent=4
        )

    print("\nContrato de schema criado com sucesso")
    print(f"Colunas KE24 encontradas: {len(ke24_columns)}")
    print(f"Arquivo gerado: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
