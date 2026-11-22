from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pandas as pd
import pytest

from tools import ke24_full_flow as flow


def _stub(monkeypatch, name, **attributes):
    module = types.ModuleType(name)
    if name in {"src", "src.ml", "src.pipeline", "src.review"}:
        module.__path__ = []
    for key, value in attributes.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def _install_model_dependencies(monkeypatch, fingerprint="a" * 64):
    _stub(monkeypatch, "src")
    prepare = _stub(monkeypatch, "src.ml")
    matrix_mod = _stub(monkeypatch, "src.ml.prepare_ke24_training_matrix",
                       build_training_matrix=lambda dataset, features: dataset[list(features)].copy())
    prepare.prepare_ke24_training_matrix = matrix_mod
    bundle = SimpleNamespace(feature_columns=("feature_a",),
                             training_fingerprint="a" * 64,
                             group_sizes={("company", "BRL"): 2},
                             models={("company", "BRL"): object()})
    _stub(monkeypatch, "src.ml.ke24_model_store",
          load_model_bundle=lambda root, version: (bundle, {"training_fingerprint": "a" * 64}))
    _stub(monkeypatch, "src.ml.ke24_training_fingerprint",
          compute_training_fingerprint=lambda matrix, context: fingerprint)
    _stub(monkeypatch, "src.pipeline")
    _stub(monkeypatch, "src.pipeline.run_ke24_drive_model", FULL_MODEL_DIR="/private/models")
    return pd.DataFrame({"company_code": ["company", "company"],
                         "currency": ["BRL", "BRL"], "feature_a": [1, 2]})


def _install_sheets_dependencies(monkeypatch, pending=1):
    _stub(monkeypatch, "src")
    _stub(monkeypatch, "src.review")
    _stub(monkeypatch, "src.review.ke24_review_sheet",
          validate_review_sheet_matrix=lambda rows: {r[0]: r for r in rows[1:]})
    _stub(monkeypatch, "src.review.ke24_review_context_sync",
          validate_context_matrix=lambda rows: {r[0]: r for r in rows[1:]})
    _stub(monkeypatch, "src.review.ke24_review_sync",
          plan_review_sheet_sync=lambda h, events, obs, trusted_snapshot:
          SimpleNamespace(new_alert_count=0, pending_request_count=pending))


def test_modelo_salvo_identico_ao_processed(monkeypatch):
    dataset = _install_model_dependencies(monkeypatch)
    result = flow.check_saved_full_model("v1", dataset)
    assert result == {"version": "v1", "rows": 2, "features": 1, "groups": 1}


def test_modelo_incompativel_bloqueia_reset(monkeypatch):
    dataset = _install_model_dependencies(monkeypatch, fingerprint="b" * 64)
    with pytest.raises(flow.FlowStopped, match="nao corresponde"):
        flow.check_saved_full_model("v1", dataset)


def test_grupos_do_modelo_incompativeis_bloqueiam(monkeypatch):
    dataset = _install_model_dependencies(monkeypatch)
    dataset.loc[0, "currency"] = "USD"
    with pytest.raises(flow.FlowStopped, match="grupos"):
        flow.check_saved_full_model("v1", dataset)


def test_superficies_alinhadas_sem_mutacao(monkeypatch):
    _install_sheets_dependencies(monkeypatch)
    history = pd.DataFrame({"alert_id": ["A1", "A2"]})
    queue = [["header"], ["A1"], ["A2"]]
    baseline = [["header"], ["A1"], ["A2"]]
    context = [["header"], ["A2"], ["A1"]]
    result = flow.check_alert_surfaces(history, queue, baseline, context)
    assert result == {"alerts": 2, "pending_reviews": 1}


def test_superficies_diferentes_bloqueiam(monkeypatch):
    _install_sheets_dependencies(monkeypatch)
    history = pd.DataFrame({"alert_id": ["A1", "A2"]})
    with pytest.raises(flow.FlowStopped, match="mesmos IDs"):
        flow.check_alert_surfaces(history, [["header"], ["A1"]],
                                  [["header"], ["A1"], ["A2"]],
                                  [["header"], ["A1"], ["A2"]])


def test_historico_vazio_bloqueia(monkeypatch):
    _install_sheets_dependencies(monkeypatch)
    with pytest.raises(flow.FlowStopped, match="ja esta vazio"):
        flow.check_alert_surfaces(pd.DataFrame({"alert_id": []}),
                                  [], [], [])


def test_novo_modo_exige_versao_exata():
    with pytest.raises(SystemExit) as exc:
        flow.main(["--prepare-new-cycle"])
    assert exc.value.code == 2


def test_preflight_dispatch_sem_exclusao(monkeypatch):
    calls = []
    monkeypatch.setattr(flow, "prepare_new_cycle", lambda version: calls.append(version))
    assert flow.main(["--prepare-new-cycle", "--model-version", "v-2026"]) == 0
    assert calls == ["v-2026"]


def test_fluxo_original_preservado(monkeypatch):
    monkeypatch.setattr(flow, "execute", lambda **kwargs:
                        flow.Summary(True, None, (), 4063))
    assert flow.main(["--preview"]) == 0
    assert flow.main(["--guided"]) == 0


def test_sem_modo_reset_destrutivo():
    import inspect
    code = inspect.getsource(flow.prepare_new_cycle)
    assert "files().delete" not in code
    assert "batchClear" not in code
