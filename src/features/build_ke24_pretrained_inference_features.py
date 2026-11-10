import numpy as np
import pandas as pd

from src.features import build_ke24_ml_features as features
from src.ml import prepare_ke24_training_matrix as training
from src.privacy.validate_protected_dataset import (
    validate_protected_dataset,
)


MEASURE_TYPES = {
    "quantity_measure",
    "financial_measure",
}

ALLOWED_TYPES = MEASURE_TYPES | {"engineered"}


def build_pretrained_inference_features(
    processed,
    training_manifest,
    privacy_policy,
):
    # Use only protected, processed KE24 records.
    validate_protected_dataset(
        processed,
        privacy_policy,
        require_all_columns=True,
    )

    if (
        not isinstance(training_manifest, pd.DataFrame)
        or training_manifest.empty
        or training_manifest.columns.duplicated().any()
    ):
        raise ValueError("Invalid training feature manifest")

    required = {
        "feature",
        "feature_type",
        "selected_for_model",
    }

    if not required.issubset(training_manifest.columns):
        raise ValueError("Incomplete training feature manifest")

    names = training_manifest["feature"]
    types = training_manifest["feature_type"]

    if (
        names.isna().any()
        or names.duplicated().any()
        or not names.map(
            lambda value: isinstance(value, str)
            and bool(value.strip())
        ).all()
        or not types.isin(ALLOWED_TYPES).all()
    ):
        raise ValueError("Invalid training feature contract")

    selected = training.get_selected_features(
        training_manifest
    )

    expected_measures = training_manifest.loc[
        types.isin(MEASURE_TYPES),
        "feature",
    ].tolist()

    expected_engineered = training_manifest.loc[
        types.eq("engineered"),
        "feature",
    ].tolist()

    # Compare the complete source measure layout,
    # including measures inactive during training.
    actual_measures = features.get_measure_columns(
        processed
    )

    if actual_measures != expected_measures:
        raise ValueError(
            "Financial measure schema differs "
            "from training manifest"
        )

    missing_context = [
        column
        for column in features.CONTEXT_COLUMNS
        if column not in processed.columns
    ]

    if missing_context:
        raise ValueError(
            "Missing inference context: "
            + ", ".join(missing_context)
        )

    measures = features.prepare_measure_matrix(
        processed,
        expected_measures,
    )

    if not np.isfinite(
        measures.to_numpy(dtype=float)
    ).all():
        raise ValueError("Non-finite KE24 measures")

    engineered = features.build_engineered_features(
        measures
    )

    if list(engineered.columns) != expected_engineered:
        raise ValueError(
            "Engineered feature contract differs "
            "from training"
        )

    available = set(expected_measures) | set(
        expected_engineered
    )

    if not set(selected).issubset(available):
        raise ValueError(
            "Unknown selected training features"
        )

    # Do not silently accept new financial movement
    # in measures that were inactive during training.
    inactive_financial = [
        name
        for name in expected_measures
        if name not in selected
        and name != "sales_quantity"
    ]

    newly_active = [
        name
        for name in inactive_financial
        if measures[name].ne(0).any()
    ]

    if newly_active:
        raise ValueError(
            "Training-inactive measures have new "
            "financial movement: "
            + ", ".join(newly_active)
        )

    # Retain training-selected measures even when
    # all their values are zero in the new batch.
    selected_measures = [
        name
        for name in expected_measures
        if name in selected
    ]

    result = pd.concat(
        [
            processed[
                features.CONTEXT_COLUMNS
            ].reset_index(drop=True),
            measures[
                selected_measures
            ].reset_index(drop=True),
            engineered.reset_index(drop=True),
        ],
        axis=1,
    )

    features.validate_output(
        processed,
        result,
        list(engineered.columns),
    )

    # Ensure all selected values remain numeric,
    # finite and compatible with the original schema.
    training.build_training_matrix(
        result,
        selected,
    )

    return result
