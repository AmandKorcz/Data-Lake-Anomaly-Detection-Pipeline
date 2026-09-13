from pathlib import Path
import pandas as pd

# ----------------------- Configurações -------------------------------
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
    / "ke24_source_profile.csv"
)

METADATA_COLUMNS = [
    "_load_id",
    "_source_file_sha256",
    "_source_system",
    "_source_file",
    "_source_row_number",
    "_ingested_at_utc",
]

# ----------------------- Profiling -------------------------------
def main():

    print()
    print("KE24 - Perfil de qualidade por origem")
    print()

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Arquivo staging não encontrado: {INPUT_FILE}"
        )

    df = pd.read_parquet(INPUT_FILE)

    if df.empty:
        raise ValueError(
            "A base KE24 não possui registros"
        )

    if "_source_file" not in df.columns:
        raise ValueError(
            "Metadado '_source_file' não encontrado"
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    ke24_columns = [
        column 
        for column in df.columns
        if column not in METADATA_COLUMNS
    ]

    profile_rows = []

    #Analisa cada arquivo separadamente
    for source_file, source_df in df.groupby(
        "_source_file",
        dropna=False
    ):
        print()
        print(f"Analisando: {source_file}")
        print(f"Registros: {len(source_df):,}")

        load_count = source_df[
            "_load_id"
        ].nunique()

        for column in ke24_columns:
            series = source_df[column]

            null_count = int(
                series.isna().sum()
            )

            non_null_count = int(
                series.notna().sum()
            )

            null_percentage = (
                null_count
                / len(source_df)
                * 100
            )

            unique_count = int(
                series.nunique(
                    dropna=True
                )
            )

            profile_rows.append({
                "source_file": source_file,
                "load_count": load_count,
                "column": column,
                "dtype": str(series.dtype),
                "row_count": len(source_df),
                "non_null_count": non_null_count,
                "null_count": null_count,
                "null_percentage": round(
                    null_percentage,
                    2
                ),
                "unique_count": unique_count
            })

    profile = pd.DataFrame(
        profile_rows
    )

    profile.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print()
    print("PERFIL POR ORIGEM CRIADO COM SUCESSO")
    print()

    print(
        f"Cargas analisadas: "
        f"{df['_source_file'].nunique()}"
    )

    print(
        f"Arquivos analisados: "
        f"{df['_load_id'].nunique()}"
    )

    print(
        f"Colunas KE24 analisadas: "
        f"{len(ke24_columns)}"
    )

    print()
    print(f"Arquivo gerado: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()