from pathlib import Path 

import pandas as pd

#Configurações
PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "ke24_processed.parquet"
)

OUTPUT_DIR = (
    PROJECT_ROOT
    / "data"
    / "curated"
)

MEASURE_PROFILE_FILE = (
    OUTPUT_DIR
    / "ke24_measure_profile.csv"
)

DIMENSION_PROFILE_FILE = (
    OUTPUT_DIR
    / "ke24_dimension_profile.csv"
)

#Perfil das medidas financeiras
def build_measure_profile(df):
    if "sales_quantity" not in df.columns:
        raise ValueError("Coluna sales_quantity não encontrada")

    measure_start_index = df.columns.get_loc(
        "sales_quantity"
    )

    measure_columns = list(
        df.columns[measure_start_index:]
    )

    profile_rows = []

    for column in measure_columns:
        values = pd.to_numeric(
            df[column],
            errors="coerce"
        )

        non_null_count = int(
            values.notna().sum()
        )

        non_zero_count = int(
            values.fillna(0).ne(0).sum()
        )

        zero_count = int(
            values.fillna(0).eq(0).sum()
        )

        non_zero_percentage = (
            non_zero_count / len(df) * 100
            if len(df) > 0 
            else 0
        )

        profile_rows.append(
            {
                "feature": column,
                "non_null_count": non_null_count,
                "non_zero_count": non_zero_count,
                "zero_count": zero_count,
                "non_zero_percentage": round(
                    non_zero_percentage, 
                    2
                ),
                "min": values.min(),
                "max": values.max(),
                "mean": values.mean(),
                "std": values.std(),
            }
        )

    profile_df = pd.DataFrame(
        profile_rows
    )

    profile_df = profile_df.sort_values(
        by=["non_zero_count", "feature"],
        ascending=[False, True]
    ).reset_index(drop=True)

    return profile_df

#Perfil das dimensões
def build_dimension_profile(df):
    technical_columns = [
        "_load_id",
        "_source_file_sha256",
        "_source_system"
        "_source_file",
        "_source_row_number",
        "_ingested_at_utc"
    ]

    temporal_columns = [
        "calendar_year",
        "period_key",
        "period_date",
    ]

    measure_start_index = df.columns.get_loc(
        "sales_quantity"
    )

    dimension_columns = list(
        df.columns[:measure_start_index]
    )

    dimension_columns = [
        column
        for column in dimension_columns
        if column not in technical_columns
        and column not in temporal_columns
    ]

    profile_rows = []

    for column in dimension_columns:
        values = df[column]

        profile_rows.append(
            {
                "dimension": column,
                "unique_values": int(
                    values.nunique(dropna=True)
                ),
                "null_count": int(values.isna().sum()),
                "null_percentage": round(
                    values.isna().mean() * 100,
                    2
                ),
            }
        )

    profile_df = pd.DataFrame(profile_rows)

    profile_df = profile_df.sort_values(
        by = [
            "unique_values",
            "dimension"
        ],
        ascending = [
            False, 
            True
        ]
    ).reset_index(drop=True)

    return profile_df

#Pipeline principal
def main():

    print("\nKE24 - Perfil de Features para ML\n")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Arquivo processed não encontrado: {INPUT_FILE}"
        )

    df = pd.read_parquet(INPUT_FILE)

    print(
        f"Registros analisados: "
        f"{len(df):,}"
    )
    print(
        f"Colunas analisadas: "
        f"{len(df.columns):,}"
    )
    
    measure_profile = (build_measure_profile(df))
    dimension_profile = (build_dimension_profile(df))

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    measure_profile.to_csv(
        MEASURE_PROFILE_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    dimension_profile.to_csv(
        DIMENSION_PROFILE_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    active_measures = (
        measure_profile[
            measure_profile["non_zero_count"] > 0
        ]
    )

    inactive_measures = (
        measure_profile[
            measure_profile["non_zero_count"] == 0
        ]
    )

    print(
        f"\nMedidas financeiras: "
        f"{len(measure_profile):,}"
    )
    print(
        f"Medidas com movimento: "
        f"{len(active_measures):,}"
    )
    print(
        f"Medidas totalmente zeradas: "
        f"{len(inactive_measures):,}"
    )

    print("\nTop 10 medidas por quantidade de registros com movimento:\n")
    print(
        active_measures[
            [
                "feature",
                "non_zero_count",
                "non_zero_percentage"
            ]
        ]
        .head(10)
        .to_string(index=False)
    )

    print("\nPerfil de medidas gerado em: ")
    print(MEASURE_PROFILE_FILE)

    print("\nPerfil de dimensões gerado em:")
    print(DIMENSION_PROFILE_FILE)

if __name__ == "__main__":
    main()    