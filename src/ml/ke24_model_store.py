
import hashlib
import json
import re
import shutil
import sys
import tempfile

from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import sklearn

from sklearn.ensemble import IsolationForest

from src.ml.ke24_grouped_model_runtime import (
    GroupedModels,
)


MODEL_FILE = "model.joblib"
MANIFEST_FILE = "manifest.json"
FORMAT_VERSION = 1


def validate_version(version):
    if not isinstance(version, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}",
        version,
    ):
        raise ValueError("Invalid model version")


def calculate_sha256(path):
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def validate_bundle(bundle):
    if not isinstance(bundle, GroupedModels):
        raise ValueError("Invalid grouped model bundle")

    features = bundle.feature_columns

    if (
        not features
        or len(features) != len(set(features))
        or not all(
            isinstance(name, str) and name
            for name in features
        )
    ):
        raise ValueError("Invalid feature contract")

    keys = set(bundle.models)

    if (
        not keys
        or keys != set(bundle.thresholds)
        or keys != set(bundle.group_sizes)
    ):
        raise ValueError("Inconsistent model groups")

    for key, model in bundle.models.items():
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or not isinstance(key[0], str)
            or not isinstance(key[1], str)
            or not re.fullmatch(
                r"TKN_[0-9a-f]{32}", key[0]
            )
            or not re.fullmatch(
                r"[A-Z]{3}", key[1]
            )
        ):
            raise ValueError("Invalid protected group")

        if not isinstance(model, IsolationForest):
            raise ValueError("Invalid model type")

        if model.n_features_in_ != len(features):
            raise ValueError("Model feature count mismatch")

        if not np.isfinite(bundle.thresholds[key]):
            raise ValueError("Invalid model threshold")

        if bundle.group_sizes[key] < 20:
            raise ValueError("Invalid training group size")


def save_model_bundle(bundle, root, version):
    validate_version(version)
    validate_bundle(bundle)

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    destination = root / version

    if destination.exists():
        raise FileExistsError(
            "Model version already exists"
        )

    temporary = Path(
        tempfile.mkdtemp(
            prefix=".model_tmp_",
            dir=root,
        )
    )

    try:
        model_path = temporary / MODEL_FILE

        joblib.dump(
            bundle,
            model_path,
            compress=3,
        )

        manifest = {
            "format_version": FORMAT_VERSION,
            "model_version": version,
            "created_at_utc": datetime.now(
                timezone.utc
            ).isoformat(),
            "python_version": (
                f"{sys.version_info.major}."
                f"{sys.version_info.minor}"
            ),
            "sklearn_version": sklearn.__version__,
            "joblib_version": joblib.__version__,
            "feature_columns": list(
                bundle.feature_columns
            ),
            "group_count": len(bundle.models),
            "training_group_sizes": sorted(
                bundle.group_sizes.values()
            ),
            "threshold_method": "training_p95",
            "model_sha256": calculate_sha256(
                model_path
            ),
        }

        (temporary / MANIFEST_FILE).write_text(
            json.dumps(
                manifest,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # Publish completed artifact as one directory.
        temporary.rename(destination)

    finally:
        if temporary.exists():
            shutil.rmtree(temporary)

    return destination


def load_model_bundle(root, version):
    validate_version(version)

    directory = Path(root) / version

    manifest_path = directory / MANIFEST_FILE
    model_path = directory / MODEL_FILE

    if (
        not manifest_path.is_file()
        or not model_path.is_file()
    ):
        raise FileNotFoundError(
            "Incomplete model version"
        )

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    expected_environment = {
        "format_version": FORMAT_VERSION,
        "model_version": version,
        "python_version": (
            f"{sys.version_info.major}."
            f"{sys.version_info.minor}"
        ),
        "sklearn_version": sklearn.__version__,
        "joblib_version": joblib.__version__,
        "threshold_method": "training_p95",
    }

    for key, expected in expected_environment.items():
        if manifest.get(key) != expected:
            raise ValueError(
                f"Incompatible model metadata: {key}"
            )

    if calculate_sha256(model_path) != manifest.get(
        "model_sha256"
    ):
        raise ValueError(
            "Model integrity check failed"
        )

    # Load only artifacts created in a trusted location.
    bundle = joblib.load(model_path)

    validate_bundle(bundle)

    if tuple(manifest["feature_columns"]) != (
        bundle.feature_columns
    ):
        raise ValueError("Stored feature contract mismatch")

    if manifest["group_count"] != len(bundle.models):
        raise ValueError("Stored group count mismatch")

    if sorted(bundle.group_sizes.values()) != (
        manifest["training_group_sizes"]
    ):
        raise ValueError("Stored group sizes mismatch")

    return bundle, manifest
