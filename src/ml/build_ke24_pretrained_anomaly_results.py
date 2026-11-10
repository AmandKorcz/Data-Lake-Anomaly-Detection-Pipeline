import pandas as pd

from src.ml import train_ke24_isolation_forest as detection
from src.ml import ke24_grouped_model_runtime as runtime
from src.ml.ke24_model_store import validate_version


def build_pretrained_anomaly_results(
    trained,
    new_matrix,
    new_context,
    *,
    model_manifest,
):
    # Check the model version and feature contract.
    if not isinstance(model_manifest, dict):
        raise ValueError("Invalid model manifest")

    version = model_manifest.get("model_version")
    validate_version(version)

    if (
        model_manifest.get("threshold_method")
        != "training_p95"
        or model_manifest.get("feature_columns")
        != list(trained.feature_columns)
        or model_manifest.get("group_count")
        != len(trained.models)
    ):
        raise ValueError("Incompatible model manifest")

    # Inference uses existing models, without fitting.
    scored = runtime.score_new_records(
        trained,
        new_matrix,
        new_context,
    )

    context = new_context.reset_index(drop=True)

    # Rank is only descriptive within this batch.
    # Alert selection must not use this batch ranking.
    ranking = context[
        ["company_code", "currency"]
    ].copy()

    ranking["score"] = scored[
        "anomaly_score"
    ].to_numpy()

    group_ranks = (
        ranking
        .groupby(["company_code", "currency"])["score"]
        .rank(method="first", ascending=False)
        .astype(int)
    )

    scores = scored.copy()

    # The operational signal uses the training P95.
    # Preserve the automatic flag for diagnostics.
    scores["anomaly_flag"] = (
        scores["reference_p95_flag"].astype(bool)
    )

    scores["anomaly_rank"] = (
        group_ranks.to_numpy()
    )

    scores["signal_method"] = "training_p95"
    scores["model_version"] = version

    return detection.build_results(
        context,
        scores,
    )
