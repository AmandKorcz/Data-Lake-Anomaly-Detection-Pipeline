import pandas as pd


IDENTITY_COLUMNS = [
    "_source_file_sha256",
    "_source_row_number",
    "company_code",
]

TOKEN_PATTERN = r"TKN_[0-9a-f]{32}"


def validate_record_identity(
    dataset,
    *,
    allow_empty=False,
):
    if not isinstance(dataset, pd.DataFrame):
        raise ValueError("Invalid identity dataset")

    missing = [
        column
        for column in IDENTITY_COLUMNS
        if column not in dataset.columns
    ]

    if missing:
        raise ValueError(
            "Missing identity columns: "
            + ", ".join(missing)
        )

    if dataset.empty:
        if allow_empty:
            return True

        raise ValueError("Empty identity dataset")

    for column in (
        "_source_file_sha256",
        "company_code",
    ):
        values = dataset[column].astype("string")

        if not values.str.fullmatch(
            TOKEN_PATTERN
        ).fillna(False).all():
            raise ValueError(
                "Invalid protected source identity"
            )

    row_numbers = pd.to_numeric(
        dataset["_source_row_number"],
        errors="coerce",
    )

    if (
        row_numbers.isna().any()
        or row_numbers.le(0).any()
        or row_numbers.mod(1).ne(0).any()
    ):
        raise ValueError(
            "Invalid source row numbers"
        )

    if dataset.duplicated(
        subset=IDENTITY_COLUMNS
    ).any():
        raise ValueError(
            "Duplicate record identities"
        )

    return True
