import pandas as pd

FILE_ID = "_source_file_sha256"

BUSINESS_KEY = [
    "company_code",
    "period",
    "period_year",
    "currency",
]

SOURCE_COLUMNS = [
    FILE_ID,
    "_source_file",
    "_source_row_number",
    *BUSINESS_KEY,
]

MANIFEST_COLUMNS = [
    FILE_ID,
    *BUSINESS_KEY,
    "record_count",
]

def _check_columns(df, required):
    if df.columns.duplicated().any():
        raise ValueError("Existem colunas duplicadas.")

    missing = set(required) - set(df.columns)

    if missing:
        raise ValueError(
            "Existem colunas obrigatórias ausentes."
        )

def _check_identifiers(df, columns):
    for column in columns:
        if df[column].isna().any():
            raise ValueError(
                f"Identificador ausente: {column}"
            )

        blank = (
            df[column]
            .astype("string")
            .str.strip()
            .eq("")
            .fillna(False)
        )

        if blank.any():
            raise ValueError(
                f"Identificador vazio: {column}"
            )

#Cria uma relação de arquivos e períodos sem incluir medidas ou valores financeiros
def build_file_manifest(staging_df):

    _check_columns(staging_df, SOURCE_COLUMNS)

    if staging_df.empty:
        raise ValueError("O staging está vazio.")

    _check_identifiers(
        staging_df,
        [
            FILE_ID,
            "_source_file",
            "_source_row_number",
            *BUSINESS_KEY,
        ],
    )

    # Não permite a mesma linha de origem duas vezes
    if staging_df.duplicated(
        [FILE_ID, "_source_row_number"]
    ).any():
        raise ValueError(
            "Há linhas de origem repetidas dentro "
            "de uma extração."
        )

    # Cada arquivo deve conter uma única combinação de empresa, período e moeda.
    consistency = (
        staging_df.groupby(FILE_ID)[
            ["_source_file", *BUSINESS_KEY]
        ]
        .nunique(dropna=False)
    )

    if consistency.gt(1).any().any():
        raise ValueError(
            "Uma extração contém mais de uma origem, "
            "empresa, período ou moeda."
        )

    # Gera uma linha de controle por arquivo
    manifest = staging_df.groupby(
        FILE_ID,
        as_index=False,
        sort=False,
    ).agg(
        company_code=("company_code", "first"),
        period=("period", "first"),
        period_year=("period_year", "first"),
        currency=("currency", "first"),
        record_count=("_source_row_number", "size"),
    )

    # Impede múltiplas versões da mesma extração
    if manifest.duplicated(BUSINESS_KEY).any():
        raise ValueError(
            "Existem duas extrações para a mesma "
            "empresa, período e moeda."
        )

    return manifest[MANIFEST_COLUMNS]   

#Compara a base local com arquivos já carregados, sem conexão com o BigQuery
def plan_incremental_load(staging_df, loaded_manifest):

    incoming = build_file_manifest(staging_df)

    # Permite simular uma tabela inicialmente vazia
    if loaded_manifest.empty and len(
        loaded_manifest.columns
    ) == 0:
        loaded = pd.DataFrame(
            columns=MANIFEST_COLUMNS
        )
    else:
        _check_columns(
            loaded_manifest,
            MANIFEST_COLUMNS,
        )

        loaded = loaded_manifest[
            MANIFEST_COLUMNS
        ].copy()

    if not loaded.empty:
        _check_identifiers(
            loaded,
            [FILE_ID, *BUSINESS_KEY, "record_count"],
        )

        counts = pd.to_numeric(
            loaded["record_count"],
            errors="coerce",
        )

        if (
            counts.isna().any()
            or counts.le(0).any()
            or counts.mod(1).ne(0).any()
        ):
            raise ValueError(
                "Manifesto remoto possui contagens inválidas."
            )

        loaded["record_count"] = counts.astype("int64")

        if (
            loaded[FILE_ID].duplicated().any()
            or loaded.duplicated(BUSINESS_KEY).any()
        ):
            raise ValueError(
                "Manifesto remoto possui arquivos "
                "ou períodos duplicados."
            )

    by_hash = {
        row[FILE_ID]: row
        for _, row in loaded.iterrows()
    }

    by_period = {
        tuple(row[col] for col in BUSINESS_KEY): row[FILE_ID]
        for _, row in loaded.iterrows()
    }

    new_hashes = set()
    skipped = 0

    for _, source in incoming.iterrows():
        hash_value = source[FILE_ID]

        business_key = tuple(
            source[col]
            for col in BUSINESS_KEY
        )

        existing = by_hash.get(hash_value)

        if existing is not None:
            existing_key = tuple(
                existing[col]
                for col in BUSINESS_KEY
            )

            if (
                existing_key != business_key
                or int(existing["record_count"])
                != int(source["record_count"])
            ):
                raise ValueError(
                    "Arquivo já carregado apresenta "
                    "metadados ou quantidade divergentes."
                )

            skipped += 1

        elif business_key in by_period:
            raise ValueError(
                "Possível substituição de extração "
                "mensal detectada; carga bloqueada."
            )

        else:
            new_hashes.add(hash_value)

    new_rows = staging_df.loc[
        staging_df[FILE_ID].isin(new_hashes)
    ].copy()

    return {
        "new_rows": new_rows,
        "new_files": len(new_hashes),
        "skipped_files": skipped,
    }
