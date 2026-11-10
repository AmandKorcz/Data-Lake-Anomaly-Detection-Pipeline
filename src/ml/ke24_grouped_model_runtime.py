from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.ml import train_ke24_isolation_forest as detection
from src.ml import prepare_ke24_training_matrix as training


GROUP_COLUMNS = ["company_code", "currency"]
REFERENCE_PERCENTILE = 95.0


@dataclass
class GroupedModels:
    feature_columns: tuple
    models: dict
    thresholds: dict
    group_sizes: dict


def validate_model_data(matrix, context):
    # Reuse existing privacy and context validations.
    detection.validate_inputs(matrix, context)

    if matrix.columns.duplicated().any():
        raise ValueError("Duplicate model features")

    required = {
        "company_code",
        "currency",
        "currency_type",
    }

    if not required.issubset(context.columns):
        raise ValueError("Missing group context")

    companies = context["company_code"].astype("string")
    currencies = context["currency"].astype("string")

    currency_types = pd.to_numeric(
        context["currency_type"],
        errors="coerce",
    )

    if not companies.str.fullmatch(
        r"TKN_[0-9a-f]{32}"
    ).fillna(False).all():
        raise ValueError("Invalid company tokens")

    if not currencies.str.fullmatch(
        r"[A-Z]{3}"
    ).fillna(False).all():
        raise ValueError("Invalid currencies")

    if not currency_types.eq(10).all():
        raise ValueError("Expected local currency type 10")

    numeric_matrix = training.build_training_matrix(
        matrix,
        list(matrix.columns),
    ).reset_index(drop=True)

    groups = context[
        GROUP_COLUMNS
    ].reset_index(drop=True).copy()

    return numeric_matrix, groups


def train_grouped_models(
    training_matrix,
    training_context,
    min_group_size=20,
    random_state=42,
):
    if min_group_size < 20:
        raise ValueError("Minimum group size must be 20")

    matrix, groups = validate_model_data(
        training_matrix,
        training_context,
    )

    models = {}
    thresholds = {}
    group_sizes = {}

    for key, positions in groups.groupby(
        GROUP_COLUMNS,
        sort=True,
    ).indices.items():

        if len(positions) < min_group_size:
            raise ValueError(
                "Insufficient training records"
            )

        group_matrix = matrix.iloc[positions]

        model = detection.train_isolation_forest(
            group_matrix,
            random_state=random_state,
        )

        # Calibrate on training data only.
        train_scores = -model.score_samples(
            group_matrix
        )

        threshold = float(
            np.percentile(
                train_scores,
                REFERENCE_PERCENTILE,
            )
        )

        models[key] = model
        thresholds[key] = threshold
        group_sizes[key] = len(positions)

    return GroupedModels(
        feature_columns=tuple(matrix.columns),
        models=models,
        thresholds=thresholds,
        group_sizes=group_sizes,
    )


def score_new_records(
    trained,
    new_matrix,
    new_context,
):
    matrix, groups = validate_model_data(
        new_matrix,
        new_context,
    )

    # Prevent feature order or schema changes.
    if tuple(matrix.columns) != trained.feature_columns:
        raise ValueError(
            "Feature contract differs from training"
        )

    test_groups = groups.groupby(
        GROUP_COLUMNS,
        sort=True,
    ).indices

    unknown_groups = (
        set(test_groups) - set(trained.models)
    )

    if unknown_groups:
        raise ValueError(
            "No trained model for one or more groups"
        )

    count = len(matrix)

    scores = np.zeros(count, dtype=float)
    automatic_flags = np.zeros(count, dtype=bool)
    reference_flags = np.zeros(count, dtype=bool)
    thresholds = np.zeros(count, dtype=float)
    sizes = np.zeros(count, dtype=int)

    for key, positions in test_groups.items():
        model = trained.models[key]
        threshold = trained.thresholds[key]

        group_matrix = matrix.iloc[positions]

        group_scores = -model.score_samples(
            group_matrix
        )

        scores[positions] = group_scores

        automatic_flags[positions] = (
            model.predict(group_matrix) == -1
        )

        reference_flags[positions] = (
            group_scores >= threshold
        )

        thresholds[positions] = threshold
        sizes[positions] = trained.group_sizes[key]

    return pd.DataFrame({
        "anomaly_score": scores,
        "anomaly_flag_auto": automatic_flags,
        "reference_p95_flag": reference_flags,
        "training_p95_threshold": thresholds,
        "model_group_size": sizes,
    })
