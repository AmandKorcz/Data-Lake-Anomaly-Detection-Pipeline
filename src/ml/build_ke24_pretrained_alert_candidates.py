import numpy as np
import pandas as pd

from src.ml.ke24_model_store import validate_version

from src.ml.evaluate_ke24_isolation_forest_stability import RANDOM_STATES

from src.ml.ke24_record_identity import (
    IDENTITY_COLUMNS,
    validate_record_identity,
)


REQUIRED_COLUMNS = IDENTITY_COLUMNS + [
    "currency",
    "currency_type",
    "anomaly_score",
    "anomaly_rank",
    "anomaly_flag",
    "reference_p95_flag",
    "training_p95_threshold",
    "model_version",
    "signal_method",
    "materiality_percentile",
    "total_absolute_value",
]


def validate_pretrained_materiality(dataset):
    if not isinstance(dataset, pd.DataFrame):
        raise ValueError("Expected a DataFrame")

    if dataset.empty:
        raise ValueError("Empty materiality dataset")

    missing = [
        column
        for column in REQUIRED_COLUMNS
        if column not in dataset.columns
    ]

    if missing:
        raise ValueError(
            "Missing candidate fields: "
            + ", ".join(missing)
        )

    validate_record_identity(dataset)

    if not (
        pd.api.types.is_bool_dtype(
            dataset["anomaly_flag"]
        )
        and pd.api.types.is_bool_dtype(
            dataset["reference_p95_flag"]
        )
    ):
        raise ValueError("Anomaly flags must be boolean")

    if (
        dataset["anomaly_flag"].isna().any()
        or dataset["reference_p95_flag"].isna().any()
    ):
        raise ValueError("Missing anomaly flags")

    scores = pd.to_numeric(
        dataset["anomaly_score"],
        errors="coerce",
    )

    thresholds = pd.to_numeric(
        dataset["training_p95_threshold"],
        errors="coerce",
    )

    if not (
        np.isfinite(scores.to_numpy(dtype=float)).all()
        and np.isfinite(
            thresholds.to_numpy(dtype=float)
        ).all()
    ):
        raise ValueError("Invalid scores or thresholds")

    expected_flags = scores.ge(thresholds)

    if not expected_flags.eq(
        dataset["reference_p95_flag"]
    ).all():
        raise ValueError(
            "Inconsistent training P95 flags"
        )

    if not dataset["anomaly_flag"].eq(
        dataset["reference_p95_flag"]
    ).all():
        raise ValueError(
            "Inconsistent operational anomaly flags"
        )

    if not dataset["signal_method"].eq(
        "training_p95"
    ).all():
        raise ValueError("Unexpected signal method")

    versions = dataset["model_version"].dropna().unique()

    if (
        len(versions) != 1
        or dataset["model_version"].isna().any()
    ):
        raise ValueError("Mixed or missing model versions")

    validate_version(versions[0])

    companies = dataset["company_code"].astype("string")
    currencies = dataset["currency"].astype("string")

    if not companies.str.fullmatch(
        r"TKN_[0-9a-f]{32}"
    ).fillna(False).all():
        raise ValueError("Invalid company tokens")

    if not currencies.str.fullmatch(
        r"[A-Z]{3}"
    ).fillna(False).all():
        raise ValueError("Invalid currency")

    currency_types = pd.to_numeric(
        dataset["currency_type"],
        errors="coerce",
    )

    if not currency_types.eq(10).all():
        raise ValueError("Expected currency_type 10")


def build_pretrained_alert_candidates(materiality):
    validate_pretrained_materiality(materiality)

    # Select exclusively by historical training P95.
    selected = materiality.loc[
        materiality["reference_p95_flag"]
    ].copy()

    selected["score_margin"] = (
        selected["anomaly_score"]
        - selected["training_p95_threshold"]
    )

    selected["candidate_threshold_value"] = (
        selected["training_p95_threshold"]
    )

    selected["candidate_reason"] = (
        "anomaly_score >= training_p95_threshold"
    )

    selected["candidate_status"] = (
        "pending_business_validation"
    )

    return (
        selected
        .sort_values(
            [
                "company_code",
                "currency",
                "anomaly_score",
                "_source_file",
                "_source_row_number",
            ],
            ascending=[
                True,
                True,
                False,
                True,
                True,
            ],
        )
        .reset_index(drop=True)
    )


def attach_pretrained_stability(candidates, stability):
    if not isinstance(candidates, pd.DataFrame):
        raise ValueError("Invalid candidates dataset")

    if not isinstance(stability, pd.DataFrame):
        raise ValueError("Invalid stability dataset")

    identity = IDENTITY_COLUMNS

    context_columns = [
        "currency",
        "currency_type",
    ]

    flag_columns = [
        f"flag_seed_{seed}"
        for seed in RANDOM_STATES
    ]

    stability_columns = [
        "mean_anomaly_score",
        "score_std",
        "flag_count",
        "mean_rank",
        "stability_rate",
        "score_seed_42",
        "flag_seed_42",
    ]

    required_candidates = identity + context_columns + [
        "anomaly_score",
        "reference_p95_flag",
    ]

    required_stability = list(dict.fromkeys(
        identity
        + context_columns
        + stability_columns
        + flag_columns
    ))

    missing_candidates = [
        col for col in required_candidates
        if col not in candidates.columns
    ]

    missing_stability = [
        col for col in required_stability
        if col not in stability.columns
    ]

    if missing_candidates or missing_stability:
        raise ValueError(
            "Missing integration fields: "
            + str(missing_candidates + missing_stability)
        )

    validate_record_identity(
        candidates,
        allow_empty=True,
    )

    validate_record_identity(stability)

    if set(stability_columns + flag_columns).intersection(
        candidates.columns
    ):
        raise ValueError(
            "Candidates already contain stability fields"
        )

    for col in flag_columns:
        if not pd.api.types.is_bool_dtype(stability[col]):
            raise ValueError("Invalid stability flag types")

        if stability[col].isna().any():
            raise ValueError("Missing stability votes")

    if not pd.api.types.is_bool_dtype(
        candidates["reference_p95_flag"]
    ):
        raise ValueError("Invalid candidate flags")

    if (
        candidates["reference_p95_flag"].isna().any()
        or not candidates["reference_p95_flag"].all()
    ):
        raise ValueError(
            "Candidates must pass training P95"
        )

    selected = stability[required_stability].copy()

    selected = selected.rename(columns={
        col: f"_stability_{col}"
        for col in context_columns
    })

    result = candidates.merge(
        selected,
        on=identity,
        how="left",
        validate="one_to_one",
    )

    if result["mean_anomaly_score"].isna().any():
        raise ValueError("Stability row not found")

    for col in ("currency",):
        expected = result[col].astype("string")
        observed = result[
            f"_stability_{col}"
        ].astype("string")

        if not expected.eq(observed).fillna(False).all():
            raise ValueError(
                "Inconsistent stability context"
            )

    expected_type = pd.to_numeric(
        result["currency_type"],
        errors="coerce",
    )

    observed_type = pd.to_numeric(
        result["_stability_currency_type"],
        errors="coerce",
    )

    if not expected_type.eq(observed_type).all():
        raise ValueError(
            "Inconsistent stability context"
        )

    numeric_columns = [
        "anomaly_score",
        "score_seed_42",
        "mean_anomaly_score",
        "score_std",
        "flag_count",
        "mean_rank",
        "stability_rate",
    ]

    numeric = result[numeric_columns].apply(
        pd.to_numeric,
        errors="coerce",
    )

    if not np.isfinite(
        numeric.to_numpy(dtype=float)
    ).all():
        raise ValueError("Invalid stability metrics")

    # The main model must match stability seed 42.
    matching_scores = np.isclose(
        numeric["anomaly_score"],
        numeric["score_seed_42"],
        rtol=0,
        atol=1e-12,
    )

    matching_flags = (
        result["reference_p95_flag"]
        == result["flag_seed_42"]
    )

    if not (
        matching_scores.all()
        and matching_flags.all()
    ):
        raise ValueError(
            "Primary model differs from stability seed 42"
        )

    expected_votes = result[flag_columns].sum(axis=1)

    if not numeric["flag_count"].eq(
        expected_votes
    ).all():
        raise ValueError("Inconsistent stability votes")

    expected_rate = (
        expected_votes / len(RANDOM_STATES) * 100
    )

    if not np.isclose(
        numeric["stability_rate"],
        expected_rate,
        rtol=0,
        atol=1e-10,
    ).all():
        raise ValueError("Inconsistent stability rate")

    if not numeric["score_std"].ge(0).all():
        raise ValueError("Invalid score deviation")

    return result.drop(columns=[
        f"_stability_{col}"
        for col in context_columns
    ])
