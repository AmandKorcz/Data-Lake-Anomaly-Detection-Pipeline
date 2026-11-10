from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.privacy.validate_protected_dataset import (
    validate_protected_dataset,
)

from src.ml import prepare_ke24_training_matrix as training
from src.ml import profile_ke24_anomaly_materiality as materiality

from src.ml.ke24_model_store import load_model_bundle

from src.ml.evaluate_ke24_isolation_forest_stability import (
    RANDOM_STATES,
)

from src.ml.build_ke24_pretrained_anomaly_results import (
    build_pretrained_anomaly_results,
)

from src.ml.build_ke24_pretrained_alert_candidates import (
    build_pretrained_alert_candidates,
    attach_pretrained_stability,
)

from src.ml.build_ke24_pretrained_stability import (
    build_pretrained_stability,
)

from src.ml.classify_ke24_pretrained_alerts import (
    build_pretrained_classification,
)

from src.alerts.build_ke24_pretrained_outputs import (
    build_pretrained_outputs,
)

from src.features.build_ke24_pretrained_inference_features import (
    build_pretrained_inference_features,
)

from src.alerts.ke24_pretrained_csv_transaction import (
    locked_pretrained_csv_pair,
    _publish_pretrained_csv_pair_locked,
)

def load_stability_model_versions(model_root, versions):
    if (
        not isinstance(versions, dict)
        or set(versions) != set(RANDOM_STATES)
    ):
        raise ValueError("Missing stability model versions")

    models = {}
    manifests = {}

    for seed in RANDOM_STATES:
        bundle, manifest = load_model_bundle(
            model_root,
            versions[seed],
        )

        # Prevent assigning a saved model to the wrong seed.
        if any(
            model.random_state != seed
            for model in bundle.models.values()
        ):
            raise ValueError("Incorrect stability model seed")

        models[seed] = bundle
        manifests[seed] = manifest

    primary = models[42]

    # All five models must use the same training dataset.
    fingerprints = {
        manifest.get("training_fingerprint")
        for manifest in manifests.values()
    }

    if len(fingerprints) != 1:
        raise ValueError(
            "Stability models use different training datasets"
        )

    # Validate feature and group compatibility.
    for seed, bundle in models.items():
        if (
            bundle.feature_columns != primary.feature_columns
            or set(bundle.models) != set(primary.models)
            or bundle.group_sizes != primary.group_sizes
        ):
            raise ValueError(
                "Incompatible stability model contracts"
            )

    return models, manifests[42]


def execute_pretrained_pipeline(
    new_features,
    feature_manifest,
    model_root,
    versions,
    protected_rules,
    privacy_policy,
    previous_history=None,
    detected_at_utc=None,
):
    if detected_at_utc is None:
        detected_at_utc = datetime.now(timezone.utc)

    # Input must already be protected.
    validate_protected_dataset(
        new_features,
        privacy_policy,
        require_all_columns=False,
    )

    models, model_manifest = (
        load_stability_model_versions(
            model_root,
            versions,
        )
    )

    primary = models[42]

    if not isinstance(feature_manifest, pd.DataFrame):
        raise ValueError("Invalid feature manifest")

    selected = training.get_selected_features(
        feature_manifest
    )

    if not set(primary.feature_columns).issubset(
        set(selected)
    ):
        raise ValueError(
            "Model features differ from training manifest"
        )

    # Use the exact feature order stored at training.
    matrix = training.build_training_matrix(
        new_features,
        list(primary.feature_columns),
    )

    anomaly_results = (
        build_pretrained_anomaly_results(
            primary,
            matrix,
            new_features,
            model_manifest=model_manifest,
        )
    )

    financial = materiality.get_financial_features(
        feature_manifest
    )

    materiality.validate_inputs(
        new_features,
        anomaly_results,
        financial,
    )

    profiled = materiality.build_materiality_dataset(
        new_features,
        anomaly_results,
        financial,
    )

    candidates = build_pretrained_alert_candidates(
        profiled
    )

    stability = build_pretrained_stability(
        models,
        matrix,
        new_features,
    )

    enriched_candidates = attach_pretrained_stability(
        candidates,
        stability,
    )

    classified = build_pretrained_classification(
        enriched_candidates,
        protected_rules,
    )

    history, dashboard = build_pretrained_outputs(
        classified,
        previous_history,
        detected_at_utc,
        privacy_policy,
    )

    return {
        "scored_count": len(new_features),
        "candidate_count": len(classified),
        "model_version": model_manifest["model_version"],
        "classified": classified,
        "history": history,
        "dashboard": dashboard,
    }

def execute_pretrained_pipeline_from_processed(
    processed,
    feature_manifest,
    model_root,
    versions,
    protected_rules,
    privacy_policy,
    previous_history=None,
    detected_at_utc=None,
):
    prepared_features = (
        build_pretrained_inference_features(
            processed,
            feature_manifest,
            privacy_policy,
        )
    )

    return execute_pretrained_pipeline(
        prepared_features,
        feature_manifest,
        model_root,
        versions,
        protected_rules,
        privacy_policy,
        previous_history=previous_history,
        detected_at_utc=detected_at_utc,
    )



def execute_pretrained_pipeline_to_files(
    new_features,
    feature_manifest,
    model_root,
    versions,
    protected_rules,
    privacy_policy,
    history_file,
    dashboard_file,
):
    history_path = Path(history_file)
    dashboard_path = Path(dashboard_file)

    if history_path.resolve() == dashboard_path.resolve():
        raise ValueError(
            "History and dashboard paths must differ"
        )

    # Lock both outputs and recover any pending transaction.
    with locked_pretrained_csv_pair(
        history_path,
        dashboard_path,
    ):
        if history_path.exists():
            previous = pd.read_csv(
                history_path,
                encoding="utf-8-sig",
            )
        else:
            previous = None

        result = execute_pretrained_pipeline(
            new_features,
            feature_manifest,
            model_root,
            versions,
            protected_rules,
            privacy_policy,
            previous_history=previous,
        )

        _publish_pretrained_csv_pair_locked(
            result["history"],
            result["dashboard"],
            history_path,
            dashboard_path,
        )

    return result
