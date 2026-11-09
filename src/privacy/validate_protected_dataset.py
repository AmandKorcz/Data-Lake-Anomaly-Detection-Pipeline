import pandas as pd

TOKEN_PATTERN = r"TKN_[0-9a-f]{32}"

REQUIRED_IDENTIFIERS = {
    "company_code",
    "_source_file",
    "_source_file_sha256",
}


def validate_protected_dataset(
    df,
    policy,
    require_all_columns=False,
):
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise ValueError("Dataset ausente ou vazio.")

    if df.columns.duplicated().any():
        raise ValueError("Existem colunas duplicadas.")

    # Campos que obrigatoriamente precisam de tokenização
    token_columns = list(policy["tokenize_dimensions"])

    token_columns += [
        column
        for column, action in policy["metadata_actions"].items()
        if action == "tokenize"
    ]

    missing_required = REQUIRED_IDENTIFIERS - set(df.columns)

    if missing_required:
        raise ValueError(
            "Identificadores obrigatórios ausentes: "
            + ", ".join(sorted(missing_required))
        )

    if require_all_columns:
        missing = set(token_columns) - set(df.columns)

        if missing:
            raise ValueError(
                "Colunas protegidas ausentes: "
                + ", ".join(sorted(missing))
            )

    invalid_columns = []

    for column in token_columns:
        if column not in df.columns:
            continue

        values = df[column].astype("string").str.strip()

        valid_tokens = (
            values.str.fullmatch(TOKEN_PATTERN)
            .fillna(False)
        )

        if column in REQUIRED_IDENTIFIERS:
            valid = valid_tokens
        else:
            valid = values.isna() | valid_tokens

        if not valid.all():
            invalid_columns.append(column)

    if invalid_columns:
        raise ValueError(
            "Identificadores sem proteção válida: "
            + ", ".join(invalid_columns)
        )

    return True
