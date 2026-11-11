import hashlib
import json

import pandas as pd


CONTEXT_COLUMNS = [
    "_source_file_sha256",
    "_source_file",
    "_source_row_number",
    "company_code",
    "currency",
    "period_key",
]


def compute_training_fingerprint(matrix, context):
    if (
        not isinstance(matrix, pd.DataFrame)
        or not isinstance(context, pd.DataFrame)
        or matrix.empty
        or len(matrix) != len(context)
    ):
        raise ValueError("Invalid training dataset")

    missing = [
        col for col in CONTEXT_COLUMNS
        if col not in context.columns
    ]

    if missing:
        raise ValueError(
            "Missing training provenance fields: "
            + ", ".join(missing)
        )

    if (
        matrix.columns.duplicated().any()
        or context.columns.duplicated().any()
        or set(matrix.columns).intersection(
            CONTEXT_COLUMNS
        )
    ):
        raise ValueError(
            "Invalid training feature contract"
        )

    metadata = context[
        CONTEXT_COLUMNS
    ].reset_index(drop=True).copy()

    if metadata.isna().any().any():
        raise ValueError(
            "Missing training provenance values"
        )

    row_numbers = pd.to_numeric(
        metadata["_source_row_number"],
        errors="coerce",
    )

    if (
        row_numbers.isna().any()
        or row_numbers.le(0).any()
        or row_numbers.mod(1).ne(0).any()
    ):
        raise ValueError("Invalid training row numbers")

    metadata["_source_row_number"] = (
        row_numbers.astype("int64")
    )

    numeric = matrix.reset_index(drop=True).apply(
        pd.to_numeric,
        errors="raise",
    )

    import numpy as np

    if not np.isfinite(
        numeric.to_numpy(dtype=float)
    ).all():
        raise ValueError(
            "Invalid training feature values"
        )

    # Bind each financial vector to its source identity.
    combined = pd.concat(
        [metadata, numeric],
        axis=1,
    )

    # Stable ordering across equivalent input batches.
    combined = combined.sort_values(
        CONTEXT_COLUMNS,
        kind="stable",
    ).reset_index(drop=True)

    digest = hashlib.sha256()

    header = [
        "KE24_TRAINING_FINGERPRINT_V1",
        list(numeric.columns),
    ]

    digest.update(
        json.dumps(
            header,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )
    digest.update(b"\n")

    for record in combined.itertuples(
        index=False,
        name=None,
    ):
        identifiers = list(
            record[:len(CONTEXT_COLUMNS)]
        )
        identifiers[2] = int(identifiers[2])

        values = [
            float(value)
            for value in record[len(CONTEXT_COLUMNS):]
        ]

        payload = identifiers + values

        digest.update(
            json.dumps(
                payload,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        )
        digest.update(b"\n")

    return digest.hexdigest()
