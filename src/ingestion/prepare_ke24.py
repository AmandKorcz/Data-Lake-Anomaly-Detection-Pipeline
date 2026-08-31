from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "database_KE24_besco.xlsx"
)

OUTPUT_DIR = PROJECT_ROOT / "data" / "staging"

OUTPUT_FILE = OUTPUT_DIR / "ke24_staging.paraquet"

def main():
    print("Iniciando a laitura da base de dados...")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Arquivo não encontrado: {INPUT_FILE}"
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(INPUT_FILE)

    print(f"Arquivo: {INPUT_FILE.name}")
    print(f"Quantidade de linhas: {len(df):,}")
    print(f"Quantidade de colunas: {len(df.columns):,}")

    if df.empty:
        raise ValueError("A base está vazia.")

    df.to_parquet(
        OUTPUT_FILE,
        index=False
    )

    print()
    print("Arquivo Parquet criado com sucesso!")
    print(f"Destino: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()