import numpy as np
import pandas as pd

from src.alerts.build_ke24_alert_records import (
    build_alert_records,
    validate_alert_input,
)

from src.alerts.persist_ke24_alert_history import (
    merge_alert_history,
)

from src.ml.build_ke24_pretrained_alert_candidates import (
    validate_pretrained_materiality,
)

from src.ml.ke24_model_store import validate_version

from src.privacy.validate_protected_dataset import (
    validate_protected_dataset,
)


IDENTITY = [
    "_source_file_sha256",
    "_source_row_number",
    "company_code",
]

RECORD_COLUMNS = [
    "alert_id",
    "company_code",
    "reference_document",
    "period_key",
    "alert_level",
    "classification_status",
    "investigation_status",
    "detected_at_utc",
    "updated_at_utc",
    "_source_file",
    "_source_file_sha256",
    "_source_row_number",
]

DETAIL_COLUMNS = [
    "profit_center",
    "product",
    "currency",
    "currency_type",
    "alert_reason",
    "anomaly_score",
    "anomaly_rank",
    "training_p95_threshold",
    "score_margin",
    "reference_p95_flag",
    "model_version",
    "signal_method",
    "total_absolute_value",
    "materiality_percentile",
    "stability_rate",
    "dominant_measure",
    "dominant_measure_value",
]

HISTORY_COLUMNS = RECORD_COLUMNS + DETAIL_COLUMNS

DASHBOARD_COLUMNS = [
    column
    for column in HISTORY_COLUMNS
    if column not in {
        "_source_file",
        "_source_file_sha256",
        "_source_row_number",
        "currency_type",
    }
]

NUMERIC_COLUMNS = [
    "anomaly_score",
    "anomaly_rank",
    "training_p95_threshold",
    "score_margin",
    "total_absolute_value",
    "materiality_percentile",
    "stability_rate",
    "dominant_measure_value",
]


def validate_pretrained_history(history, policy):
    if history.empty:
        return

    if "anomaly_score_percentile" in history.columns:
        raise ValueError(
            "Legacy percentile history is not supported"
        )

    missing = [
        col for col in HISTORY_COLUMNS
        if col not in history.columns
    ]

    if missing:
        raise ValueError(
            "Missing pretrained history fields: "
            + ", ".join(missing)
        )

    validate_alert_input(history)

    validate_protected_dataset(
        history,
        policy,
        require_all_columns=False,
    )

    if history["alert_id"].duplicated().any():
        raise ValueError("Duplicate alert IDs")

    token_pattern = r"TKN_[0-9a-f]{32}"

    for column in (
        "profit_center",
        "product",
        "reference_document",
    ):
        values = history[column].astype("string")

        valid = (
            values.isna()
            | values.str.fullmatch(
                token_pattern
            ).fillna(False)
        )

        if not valid.all():
            raise ValueError(
                f"Unprotected identifier: {column}"
            )

    numeric = history[NUMERIC_COLUMNS].apply(
        pd.to_numeric,
        errors="coerce",
    )

    if not np.isfinite(
        numeric.to_numpy(dtype=float)
    ).all():
        raise ValueError("Invalid dashboard metrics")

    if not np.isclose(
        numeric["score_margin"],
        numeric["anomaly_score"]
        - numeric["training_p95_threshold"],
        rtol=0,
        atol=1e-12,
    ).all():
        raise ValueError("Inconsistent score margin")

    if not numeric["score_margin"].ge(0).all():
        raise ValueError("Negative score margin")

    if not history["signal_method"].eq(
        "training_p95"
    ).all():
        raise ValueError("Invalid signal method")

    flags = history["reference_p95_flag"]

    if (
        not pd.api.types.is_bool_dtype(flags)
        or flags.isna().any()
        or not flags.all()
    ):
        raise ValueError("Invalid P95 flags")

    if history["model_version"].isna().any():
        raise ValueError("Missing model version")

    for version in history["model_version"].unique():
        validate_version(version)

    for date_column in (
        "detected_at_utc",
        "updated_at_utc",
    ):
        dates = pd.to_datetime(
            history[date_column],
            utc=True,
            errors="coerce",
        )

        if dates.isna().any():
            raise ValueError("Invalid alert timestamps")


def build_pretrained_dashboard(history, policy):
    validate_pretrained_history(history, policy)

    missing = [
        col for col in DASHBOARD_COLUMNS
        if col not in history.columns
    ]

    if missing:
        raise ValueError(
            "Missing dashboard fields: "
            + ", ".join(missing)
        )

    dashboard = history[DASHBOARD_COLUMNS].copy()

    if dashboard.empty:
        dashboard["detection_date"] = pd.Series(
            dtype="string"
        )
    else:
        dates = pd.to_datetime(
            dashboard["detected_at_utc"],
            utc=True,
        )

        dashboard["detection_date"] = (
            dates.dt.strftime("%Y-%m-%d")
        )

    dashboard["alert_count"] = pd.Series(
        [1] * len(dashboard),
        index=dashboard.index,
        dtype="int64",
    )

    return dashboard


def build_pretrained_outputs(
    classified,
    previous_history,
    detected_at_utc,
    policy,
):
    if not isinstance(classified, pd.DataFrame):
        raise ValueError("Invalid classification dataset")

    missing = [
        col
        for col in (
            [
                "_source_file",
                "reference_document",
                "period_key",
                "alert_level",
                "classification_status",
                "anomaly_flag",
            ]
            + IDENTITY
            + DETAIL_COLUMNS
        )
        if col not in classified.columns
    ]

    if missing:
        raise ValueError(
            "Missing classification fields: "
            + ", ".join(missing)
        )

    if previous_history is None:
        previous = pd.DataFrame(
            columns=HISTORY_COLUMNS
        )
    elif isinstance(previous_history, pd.DataFrame):
        previous = previous_history.copy()
    else:
        raise ValueError("Invalid previous history")

    validate_pretrained_history(previous, policy)

    # No new candidates is a valid inference result.
    if classified.empty:
        history = (
            previous.copy()
            if not previous.empty
            else pd.DataFrame(columns=HISTORY_COLUMNS)
        )

        return (
            history,
            build_pretrained_dashboard(history, policy),
        )

    validate_pretrained_materiality(classified)

    records = build_alert_records(
        classified,
        detected_at_utc,
    )

    details = classified[
        IDENTITY + DETAIL_COLUMNS
    ].copy()

    current = records.merge(
        details,
        on=IDENTITY,
        how="left",
        validate="one_to_one",
    )

    validate_pretrained_history(current, policy)

    history = merge_alert_history(
        previous,
        current,
    )

    validate_pretrained_history(history, policy)

    dashboard = build_pretrained_dashboard(
        history,
        policy,
    )

    return history, dashboard
