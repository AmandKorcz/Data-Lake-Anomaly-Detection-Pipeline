from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import pandas as pd
import pytest


SOURCE = Path(__file__).resolve().parents[1] / "src/pipeline/run_ke24_drive_model.py"


def _stub(monkeypatch, name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


@pytest.fixture
def model_module(monkeypatch):
    # O modulo real importa dependencias cloud; os testes nao se conectam.
    _stub(monkeypatch, "googleapiclient")
    _stub(monkeypatch, "googleapiclient.discovery", build=lambda *_a, **_k: "drive")
    _stub(monkeypatch, "src.ingestion.google_drive_client", authenticate=lambda: "auth")
    training_stub = _stub(
        monkeypatch, "src.ml.prepare_ke24_training_matrix",
        build_training_matrix=lambda df, columns: df[list(columns)].copy(),
    )
    _stub(monkeypatch, "src.ml", prepare_ke24_training_matrix=training_stub)
    _stub(
        monkeypatch, "src.ml.ke24_grouped_model_runtime",
        score_new_records=lambda **_k: None,
        train_grouped_models=lambda **_k: None,
    )
    _stub(
        monkeypatch, "src.pipeline.run_ke24_drive_features",
        build_features_in_memory=lambda *_a: None,
        load_verified_processed_dataframe=lambda *_a: None,
    )
    _stub(monkeypatch, "src.privacy.privacy_runtime", load_privacy_runtime=lambda: ({}, {}, b"key"))
    _stub(
        monkeypatch, "src.privacy.validate_protected_dataset",
        validate_protected_dataset=lambda *_a, **_k: None,
    )
    module_name = "ke24_full_retrain_test_module"
    spec = importlib.util.spec_from_file_location(module_name, SOURCE)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def _scenario():
    data = pd.DataFrame({"f1": [1., 2., 3., 4.], "f2": [100., 200., 300., 400.]})
    temporal = {
        "train_matrix": data[["f2", "f1"]].iloc[:2].reset_index(drop=True),
        "train_context": data.iloc[:2],
        "validation_context": data.iloc[2:],
        "model": SimpleNamespace(models={("company", "BRL"): object()}),
    }
    return data, temporal


def test_full_matrix_includes_holdout_and_preserves_feature_order(model_module):
    data, temporal = _scenario()
    matrix = model_module.prepare_full_training_matrix(data, temporal)
    assert len(matrix) == 4
    assert list(matrix.columns) == ["f2", "f1"]
    assert matrix["f1"].tolist() == [1., 2., 3., 4.]


def test_full_matrix_rejects_missing_rows(model_module):
    data, temporal = _scenario()
    with pytest.raises(RuntimeError, match="difere da divisao temporal"):
        model_module.prepare_full_training_matrix(data.iloc[:3], temporal)


def test_full_model_trains_with_all_rows_not_only_temporal(model_module, monkeypatch):
    data, temporal = _scenario()
    observed = {}

    def fake_train(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(
            feature_columns=tuple(kwargs["training_matrix"].columns),
            models={("company", "BRL"): object()},
            group_sizes={("company", "BRL"): 4},
            training_fingerprint="b" * 64,
        )

    monkeypatch.setattr(model_module, "train_grouped_models", fake_train)
    bundle, rows = model_module.train_full_model_in_memory(data, temporal)
    assert rows == 4
    assert len(observed["training_context"]) == 4
    assert observed["training_matrix"]["f1"].tolist() == [1., 2., 3., 4.]
    assert observed["random_state"] == 42
    assert observed["min_group_size"] == 20
    assert sum(bundle.group_sizes.values()) == 4


def test_full_model_blocks_changed_group_set(model_module, monkeypatch):
    data, temporal = _scenario()
    monkeypatch.setattr(
        model_module, "train_grouped_models",
        lambda **_k: SimpleNamespace(
            feature_columns=("f2", "f1"),
            models={("different", "BRL"): object()},
            group_sizes={("different", "BRL"): 4},
        ),
    )
    with pytest.raises(RuntimeError, match="inconsistente"):
        model_module.train_full_model_in_memory(data, temporal)


def test_version_deterministic_and_valid(model_module):
    instant = datetime(2026, 11, 22, 16, 30, 0, tzinfo=timezone.utc)
    version = model_module.build_full_model_version("a" * 64, now=instant)
    assert version == "ke24_full_20261122T163000000000Z_aaaaaaaaaa"
    with pytest.raises(ValueError):
        model_module.build_full_model_version("not-a-hash", now=instant)


def test_saving_requires_explicit_flags(model_module):
    with pytest.raises(SystemExit) as exc:
        model_module.main(["--save-model"])
    assert exc.value.code == 2


def test_save_model_checks_loaded_version(model_module, monkeypatch, tmp_path):
    bundle = SimpleNamespace(
        training_fingerprint="d" * 64,
        feature_columns=("f1", "f2"),
        group_sizes={("company", "BRL"): 21},
    )
    saved = {}

    def fake_save(b, root, version):
        saved.update(root=root, version=version, bundle=b)
        return root / version

    def fake_load(root, version):
        return bundle, {"model_version": version}

    _stub(monkeypatch, "src.ml.ke24_model_store", save_model_bundle=fake_save, load_model_bundle=fake_load)
    path, version = model_module.save_full_model(bundle, root=tmp_path)
    assert saved["bundle"] is bundle
    assert saved["root"] == tmp_path
    assert path == tmp_path / version
    assert version.startswith("ke24_full_")


def test_save_model_blocks_corrupted_reload(model_module, monkeypatch, tmp_path):
    bundle = SimpleNamespace(
        training_fingerprint="c" * 64,
        feature_columns=("f1",),
        group_sizes={("company", "BRL"): 21},
    )
    _stub(
        monkeypatch, "src.ml.ke24_model_store",
        save_model_bundle=lambda b, root, version: root / version,
        load_model_bundle=lambda root, version: (
            SimpleNamespace(
                training_fingerprint="e" * 64,
                feature_columns=bundle.feature_columns,
                group_sizes=bundle.group_sizes,
            ),
            {"model_version": version},
        ),
    )
    with pytest.raises(RuntimeError, match="reprovada"):
        model_module.save_full_model(bundle, root=tmp_path)
