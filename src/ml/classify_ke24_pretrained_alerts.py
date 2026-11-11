import numpy as np
import pandas as pd

from src.ml import classify_ke24_alerts as legacy

from src.ml.build_ke24_pretrained_alert_candidates import (
    validate_pretrained_materiality,
)


REQUIRED_COLUMNS = [
    "company_code",
    "currency",
    "currency_type",
    "anomaly_score",
    "training_p95_threshold",
    "score_margin",
    "stability_rate",
    "total_absolute_value",
    "materiality_percentile",
    "reference_p95_flag",
    "reference_document",
]

CLASSIFICATION_COLUMNS = [
    "materiality_level",
    "materiality_reason",
    "anomaly_signal_level",
    "anomaly_signal_reason",
    "stability_level",
    "stability_reason",
    "alert_level",
    "alert_reason",
    "classification_status",
]


def classify_pretrained_signal(row, rules):
    technical = rules.get(
        "technical_thresholds", {}
    )

    medium = technical.get(
        "pretrained_margin_medium_min"
    )
    high = technical.get(
        "pretrained_margin_high_min"
    )

    # Never invent thresholds for real data.
    if medium is None or high is None:
        return (
            "PENDING_TECHNICAL_RULE",
            "pretrained_margin_thresholds_not_defined",
        )

    try:
        medium = float(medium)
        high = float(high)
    except (TypeError, ValueError):
        raise ValueError(
            "Invalid pretrained margin thresholds"
        ) from None

    if not (
        np.isfinite(medium)
        and np.isfinite(high)
        and 0 <= medium < high
    ):
        raise ValueError(
            "Invalid pretrained margin thresholds"
        )

    margin = float(row["score_margin"])

    if margin >= high:
        return "HIGH", "high_pretrained_score_margin"

    if margin >= medium:
        return "MEDIUM", "medium_pretrained_score_margin"

    return "LOW", "low_pretrained_score_margin"


def build_pretrained_classification(
    candidates,
    rules,
):
    if not isinstance(candidates, pd.DataFrame):
        raise ValueError("Invalid candidates dataset")

    if not isinstance(rules, dict):
        raise ValueError("Invalid classification rules")

    missing = [
        column
        for column in REQUIRED_COLUMNS
        if column not in candidates.columns
    ]

    if missing:
        raise ValueError(
            "Missing classification fields: "
            + ", ".join(missing)
        )

    result = candidates.copy()

    # No candidates is a valid inference outcome.
    if result.empty:
        for column in CLASSIFICATION_COLUMNS:
            result[column] = pd.Series(dtype="string")

        return result

    validate_pretrained_materiality(result)

    # All inputs must already be selected candidates.
    if not result["reference_p95_flag"].all():
        raise ValueError("Unselected anomaly candidate")

    margins = pd.to_numeric(
        result["score_margin"],
        errors="coerce",
    )

    expected_margin = (
        pd.to_numeric(
            result["anomaly_score"],
            errors="coerce",
        )
        - pd.to_numeric(
            result["training_p95_threshold"],
            errors="coerce",
        )
    )

    if not (
        np.isfinite(margins.to_numpy(dtype=float)).all()
        and np.isclose(
            margins.to_numpy(dtype=float),
            expected_margin.to_numpy(dtype=float),
            rtol=0,
            atol=1e-12,
        ).all()
        and margins.ge(0).all()
    ):
        raise ValueError("Inconsistent score margin")

    if not isinstance(
        rules.get("business_thresholds_by_company"),
        dict,
    ):
        raise ValueError("Missing company rules")

    legacy.validate_currency_basis(result, rules)

    # Keep business review separate from ML classification.
    if "review_status" not in result.columns:
        result["review_status"] = "pending"

    if "business_validation" not in result.columns:
        result["business_validation"] = pd.NA

    # Reuse the existing financial rules.
    financial = result.apply(
        lambda row: legacy.classify_materiality(
            row, rules
        ),
        axis=1,
    )

    result["materiality_level"] = [
        value[0] for value in financial
    ]

    result["materiality_reason"] = [
        value[1] for value in financial
    ]

    # Use the historical P95 score margin,
    # not a percentile calculated on this batch.
    signals = result.apply(
        lambda row: classify_pretrained_signal(
            row, rules
        ),
        axis=1,
    )

    result["anomaly_signal_level"] = [
        value[0] for value in signals
    ]

    result["anomaly_signal_reason"] = [
        value[1] for value in signals
    ]

    # Reuse stability classification rules.
    stability = result.apply(
        lambda row: legacy.classify_stability(
            row, rules
        ),
        axis=1,
    )

    result["stability_level"] = [
        value[0] for value in stability
    ]

    result["stability_reason"] = [
        value[1] for value in stability
    ]

    alert_levels = result.apply(
        legacy.classify_alert_level,
        axis=1,
    )

    result["alert_level"] = [
        value[0] for value in alert_levels
    ]

    result["alert_reason"] = [
        value[1] for value in alert_levels
    ]

    result["classification_status"] = result.apply(
        lambda row: legacy.determine_classification_status(
            row, rules
        ),
        axis=1,
    )

    return result
