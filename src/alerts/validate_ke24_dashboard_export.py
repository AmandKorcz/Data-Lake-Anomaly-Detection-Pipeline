import numpy as np
import pandas as pd


EXPECTED_COLUMNS = {
    "alert_id",
    "period_key",
    "company_code",
    "profit_center",
    "product",
    "reference_document",
    "currency",
    "alert_level",
    "alert_reason",
    "classification_status",
    "investigation_status",
    "anomaly_score",
    "anomaly_score_percentile",
    "anomaly_rank",
    "total_absolute_value",
    "materiality_percentile",
    "stability_rate",
    "dominant_measure",
    "dominant_measure_value",
    "detected_at_utc",
    "updated_at_utc",
    "detection_date",
    "alert_count",
}

TOKEN_COLUMNS = {
    "company_code": False,
    "profit_center": True,
    "product": True,
    "reference_document": True,
}

NUMERIC_COLUMNS = {
    "anomaly_score",
    "anomaly_score_percentile",
    "anomaly_rank",
    "total_absolute_value",
    "materiality_percentile",
    "stability_rate",
    "dominant_measure_value",
    "alert_count",
}


def validate_dashboard_export(dataset):
    if not isinstance(dataset, pd.DataFrame) or dataset.empty:
        raise ValueError("Dashboard dataset is empty or invalid")

    if dataset.columns.duplicated().any():
        raise ValueError("Duplicate dashboard columns")

    missing = EXPECTED_COLUMNS - set(dataset.columns)
    unexpected = set(dataset.columns) - EXPECTED_COLUMNS

    if missing or unexpected:
        raise ValueError(
            f"Invalid dashboard schema. "
            f"Missing: {sorted(missing)}. "
            f"Unexpected: {sorted(unexpected)}"
        )

    for column, allow_null in TOKEN_COLUMNS.items():
        values = dataset[column].astype("string")

        valid_tokens = (
            values.str.fullmatch(r"TKN_[0-9a-f]{32}")
            .fillna(False)
        )

        valid = (
            values.isna() | valid_tokens
            if allow_null
            else valid_tokens
        )

        if not valid.all():
            raise ValueError(
                f"Unprotected identifier: {column}"
            )

    alert_ids = dataset["alert_id"].astype("string")

    if not alert_ids.str.fullmatch(
        r"ALT_[0-9a-f]{32}"
    ).fillna(False).all():
        raise ValueError("Invalid alert IDs")

    if alert_ids.duplicated().any():
        raise ValueError("Duplicate alert IDs")

    for column in NUMERIC_COLUMNS:
        values = pd.to_numeric(
            dataset[column],
            errors="coerce",
        )

        if not np.isfinite(
            values.to_numpy(dtype=float)
        ).all():
            raise ValueError(
                f"Invalid numeric field: {column}"
            )

    if not pd.to_numeric(
        dataset["alert_count"]
    ).eq(1).all():
        raise ValueError("Invalid alert_count")

    return True
