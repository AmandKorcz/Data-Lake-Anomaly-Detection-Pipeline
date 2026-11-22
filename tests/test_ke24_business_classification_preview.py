"""Testes locais: nao usam Drive, Sheets ou regras financeiras reais."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from src.pipeline.ke24_business_classification_preview import (
    summarize_classified_candidates,
)
from src.pipeline import run_ke24
from src.pipeline.ke24_preview_execution_guard import read_preview_report


def _classified():
    return pd.DataFrame([
        {"alert_level": "HIGH", "classification_status": "EXPERIMENTAL_CLASSIFICATION"},
        {"alert_level": "MEDIUM", "classification_status": "PENDING_BUSINESS_RULE"},
        {"alert_level": "PENDING", "classification_status": "PENDING_TECHNICAL_RULE"},
        {"alert_level": "LOW", "classification_status": "READY_FOR_FINAL_CLASSIFICATION"},
        {"alert_level": "HIGH", "classification_status": "PENDING_BUSINESS_VALIDATION"},
    ])


def test_summary_contains_only_counts():
    result = summarize_classified_candidates(_classified(), 5)
    assert result == {
        "classification_total": 5,
        "classification_pending": 3,
        "classification_experimental": 1,
        "classification_ready": 1,
        "priority_high": 2,
        "priority_medium": 1,
        "priority_low": 1,
    }
    assert all(type(value) is int for value in result.values())


def test_empty_classification_is_valid():
    result = summarize_classified_candidates(
        pd.DataFrame(columns=["alert_level", "classification_status"]), 0
    )
    assert all(value == 0 for value in result.values())


@pytest.mark.parametrize("bad", [
    pd.DataFrame([{"alert_level": "HIGH", "classification_status": "NOT_A_STATUS"}]),
    pd.DataFrame([{"alert_level": "URGENT", "classification_status": "PENDING_BUSINESS_RULE"}]),
    pd.DataFrame([{"alert_level": None, "classification_status": "PENDING_BUSINESS_RULE"}]),
    pd.DataFrame([{"alert_level": "HIGH"}]),
])
def test_invalid_classification_is_blocked(bad):
    with pytest.raises(ValueError):
        summarize_classified_candidates(bad, 1)


def test_mismatched_candidate_count_is_blocked():
    with pytest.raises(ValueError, match="quantidade"):
        summarize_classified_candidates(_classified(), 4)


def _adapters(candidates, *, rules=None, load_error=None):
    actions = []

    def rules_loader():
        actions.append("rules")
        if load_error is not None:
            raise load_error
        return rules

    def model(*args, **kwargs):
        actions.append("model")
        assert len(args) == 3
        if "protected_rules" in kwargs:
            assert kwargs["protected_rules"] is rules
            return {
                "candidates": candidates,
                "candidate_count": len(candidates),
                "stable_all_seeds": 1,
                "classified": _classified().iloc[:len(candidates)].copy(),
            }
        return {"candidates": candidates, "candidate_count": len(candidates), "stable_all_seeds": 1}

    def pending(candidates_received, *args):
        actions.append("pending")
        assert "alert_level" not in candidates_received.columns
        return {"history": candidates_received}

    adapters = SimpleNamespace(
        load_processed=lambda *args: pd.DataFrame({"safe": [1]}),
        build_features=lambda *args: (pd.DataFrame({"safe": [1]}), "features", {"train_rows": 1, "validation_rows": 1}),
        build_alert_preview=model,
        build_pending_outputs=pending,
        load_history=lambda *args: SimpleNamespace(history=pd.DataFrame({"safe": []})),
        append_alerts=lambda old, cur, *a: (cur, len(cur)),
        sheet_matrix=lambda *args: [["header"], [1]],
        load_rules=rules_loader,
    )
    return adapters, actions


def test_explicit_classification_reuses_ml_and_leaves_history_pending():
    candidates = pd.DataFrame({"safe": [10, 20]})
    rules = {"fictional": "approved test only"}
    adapters, actions = _adapters(candidates, rules=rules)
    result = run_ke24.execute_readonly_stages(
        object(), {}, {}, b"test-secret", adapters=adapters,
        source_audit=lambda *args: {"raw_files": 4, "processed_files": 4},
        classify=True,
    )
    assert actions == ["rules", "model", "pending"]
    assert result["classification_total"] == 2
    assert result["classification_pending"] == 1
    assert result["candidate_count"] == 2


def test_default_preview_does_not_load_rules():
    candidates = pd.DataFrame({"safe": [10, 20]})
    adapters, actions = _adapters(candidates)
    result = run_ke24.execute_readonly_stages(
        object(), {}, {}, b"key", adapters=adapters,
        source_audit=lambda *args: {"raw_files": 4, "processed_files": 4},
    )
    assert actions == ["model", "pending"]
    assert "classification_total" not in result


def test_missing_rules_blocks_before_model_and_history():
    candidates = pd.DataFrame({"safe": [10]})
    adapters, actions = _adapters(candidates, load_error=FileNotFoundError("LOCAL_PATH_MUST_BE_HIDDEN"))
    with pytest.raises(run_ke24.PreviewBlocked, match="Regras financeiras privadas") as exc:
        run_ke24.execute_readonly_stages(
            object(), {}, {}, b"key", adapters=adapters,
            source_audit=lambda *args: {"raw_files": 4, "processed_files": 4},
            classify=True,
        )
    assert "LOCAL_PATH_MUST_BE_HIDDEN" not in str(exc.value)
    assert actions == ["rules"]


def test_cli_rejects_classify_without_preview():
    with pytest.raises(SystemExit) as exc:
        run_ke24.main(["--dry-run", "--classify"])
    assert exc.value.code == 2


def test_cli_passes_classify_flag(monkeypatch):
    seen = []
    monkeypatch.setattr(run_ke24, "run_preview", lambda **kw: seen.append(kw) or 0)
    assert run_ke24.main(["--preview", "--classify"]) == 0
    assert seen == [{"classify": True}]


def test_report_writes_only_counts_and_no_private_data(tmp_path):
    from src.pipeline.ke24_preview_execution_guard import write_preview_report
    write_preview_report(
        tmp_path, run_id="test123", started_at=datetime.now(timezone.utc),
        duration_seconds=2.0, status="SUCCESS", result={
            **summarize_classified_candidates(_classified(), 5),
            "company_code": "TKN_must_not_log",
            "private_threshold": 999999,
        },
    )
    report = read_preview_report(tmp_path)
    assert report["metrics"]["classification_total"] == 5
    assert "company_code" not in report["metrics"]
    assert "private_threshold" not in report["metrics"]
