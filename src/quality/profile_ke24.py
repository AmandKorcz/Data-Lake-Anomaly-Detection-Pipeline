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
    / "ke24_data_profile.csv"
)

METADATA_COLUMNS = [
    "_load_id",
    "_source_file_sha256",
    "_source_file",
    "_source_row_number",
    "_ingested_at_utc"
]

# ----------------------- Perfil dos dados -------------------------------
def main():
    print("\nKE24 - Perfil de qualidade dos dados\n")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Arquivo de staging não encontrado: {INPUT_FILE}"
        )

    df = pd.read_parquet(INPUT_FILE)

    if df.empty:
        raise ValueError(
            "A base KE24 não possui registros"
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    profile_rows = []

    for column in df.columns:
        series = df[column]
        null_count = int(series.isna().sum())

        null_percentage = (
            null_count /len(df)
        ) * 100

        non_null_count = int(
            series.notna().sum()
        )

        unique_count = int(
            series.nunique(dropna=True)
        )

        profile_rows.append({
            "column": column,
            "dtype": str(series.dtype),
            "row_count": non_null_count,
            "null_count": null_count,
            "null_percentage": round(
                null_percentage,
                2
            ),
            "unique_count": unique_count,
            "is_metadata": (
                column in METADATA_COLUMNS
            ),
        })

    profile = pd.DataFrame(
        profile_rows
    )

    profile.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print(
        f"Registros analisados: {len(df):,}"
    )

    print(
        f"Colunas analisadas: {len(df):,}"
    )

    print("\nTipos encontrados: ")

    print(
        profile["dtype"]
        .value_counts()
        .to_string()
    )

    print("\n Colunas com valores nulos: ")

    columns_with_nulls = (
        profile[
            profile["null_count"] > 0
        ]
        .sort_values(
            "null_percentage",
            ascending=False
        )
    )

    if columns_with_nulls.empty:
        print(
            "Nenhuma coluna possui valores nulos"
        )

    else:
        print(
            columns_with_nulls[
                [
                    "column",
                    "null_count",
                    "null_percentage"
                ]
            ]
            .to_string(index=False)
        )

    print("\n Perfil criado com sucesso!")

    print(f"Arquivo gerado: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()

