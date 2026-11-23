from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from src.pipeline import run_ke24
from src.pipeline.ke24_business_checks_bridge import (
    SAFE_BUSINESS_KEYS,
    run_readonly_business_checks,
    summarize_readonly_business_checks,
)
from src.pipeline.ke24_preview_execution_guard import (
    read_preview_report, write_preview_report,
)

TOKEN = "TKN_" + "a" * 32


def _synthetic_result():
    rows = []
    for status, reason, kind in [
        ("PASS", "within_historical_band", "revenue"),
        ("REVIEW_MEDIUM", "monthly_change_medium", "revenue"),
        ("REVIEW_HIGH", "monthly_change_high", "material_cost_components"),
        ("VIOLATION", "unexpected_company_currency", "company_currency"),
        ("PENDING", "insufficient_consecutive_history", "revenue"),
        ("PENDING", "insufficient_consecutive_history", "material_cost_components"),
        ("PENDING", "zero_historical_baseline", "revenue"),
        ("PENDING", "company_currency_not_configured", "company_currency"),
    ]:
        rows.append({
            "rule_id": "KE24-BC-001", "check_type": kind,
            "status": status, "reason": reason,
            "company_code": TOKEN, "currency": "MXN", "period_key": "2026-04",
            "deviation_ratio": 0.65,
        })
    return pd.DataFrame(rows)


def test_safe_diagnostic_counts_only():
    summary = summarize_readonly_business_checks(_synthetic_result())
    assert tuple(summary) == SAFE_BUSINESS_KEYS
    assert summary["business_checks_total"] == 8
    assert summary["business_checks_pending"] == 4
    assert summary["business_pending_history"] == 2
    assert summary["business_pending_zero_baseline"] == 1
    assert summary["business_pending_company_currency"] == 1
    assert summary["business_pending_revenue"] == 2
    assert summary["business_pending_material"] == 1
    assert all(type(value) is int for value in summary.values())
    assert TOKEN not in str(summary)
    assert "MXN" not in str(summary)


def test_rejects_unknown_status_and_reason():
    bad = _synthetic_result()
    bad.loc[0, "status"] = "UNKNOWN"
    with pytest.raises(ValueError):
        summarize_readonly_business_checks(bad)
    bad = _synthetic_result()
    bad.loc[4, "reason"] = "unexpected_pending"
    with pytest.raises(ValueError):
        summarize_readonly_business_checks(bad)


def test_bridge_uses_processed_without_mutating_and_returns_only_summary(monkeypatch):
    import src.pipeline.ke24_business_checks_bridge as mod
    processed = pd.DataFrame({"private": [123]})
    original = processed.copy(deep=True)
    monkeypatch.setattr(mod, "load_policy", lambda: {"test": True})
    monkeypatch.setattr(mod, "load_company_currency_rules", lambda key: {TOKEN: ["MXN"]})
    def check(df, policy, *, company_currencies):
        assert df is processed
        assert policy == {"test": True}
        assert company_currencies == {TOKEN: ["MXN"]}
        return _synthetic_result()
    monkeypatch.setattr(mod, "evaluate_business_checks", check)
    summary = run_readonly_business_checks(processed, b"dummy")
    assert summary["business_checks_total"] == 8
    pd.testing.assert_frame_equal(processed, original)


def test_summary_is_persisted_without_private_data(tmp_path):
    summary = summarize_readonly_business_checks(_synthetic_result())
    write_preview_report(
        tmp_path, run_id="synthetic123", started_at=datetime.now(timezone.utc),
        duration_seconds=1.0, status="SUCCESS",
        result={**summary, "secret": "SECRET_DO_NOT_LOG", "company": TOKEN},
    )
    report = read_preview_report(tmp_path)
    assert report["metrics"]["business_checks_pending"] == 4
    raw = (tmp_path / "data/curated/ke24_preview_last_run.json").read_text(encoding="utf-8")
    assert "SECRET_DO_NOT_LOG" not in raw
    assert TOKEN not in raw


def test_orchestrator_executes_business_stage_once_and_before_ml(caplog):
    calls = []
    processed = pd.DataFrame({"safe": [1, 2]})
    candidates = pd.DataFrame({"id": [1]})
    def visit(name, value):
        def action(*args, **kwargs):
            calls.append(name)
            return value
        return action
    adapters = SimpleNamespace(
        load_processed=visit("processed", processed),
        run_business_checks=visit("business", summarize_readonly_business_checks(_synthetic_result())),
        build_features=visit("features", (processed, None, {"train_rows": 1, "validation_rows": 1})),
        build_alert_preview=visit("model", {"candidates": candidates, "candidate_count": 1, "stable_all_seeds": 1}),
        build_pending_outputs=visit("pending", {"history": candidates}),
        load_history=visit("history", SimpleNamespace(history=pd.DataFrame({"id": []}))),
        append_alerts=visit("merge", (candidates, 1)),
        sheet_matrix=visit("sheets", [["header"], ["row"]]),
    )
    result = run_ke24.execute_readonly_stages(
        object(), {}, {}, b"secret", adapters=adapters,
        source_audit=visit("audit", {"raw_files": 1, "processed_files": 1}),
    )
    assert calls == ["audit", "processed", "business", "features", "model", "pending", "history", "merge", "sheets"]
    assert result["business_checks_total"] == 8
    assert result["candidate_count"] == 1
    assert result["history_new"] == 1
    assert TOKEN not in caplog.text


def test_orchestrator_blocks_on_invalid_business_summary():
    adapters = SimpleNamespace(
        load_processed=lambda *a: pd.DataFrame({"safe": [1]}),
        run_business_checks=lambda *a: {"company": TOKEN},
        build_features=lambda *a: pytest.fail("ML nao deve executar apos checks invalidos"),
    )
    with pytest.raises(ValueError, match="Resumo"):
        run_ke24.execute_readonly_stages(
            object(), {}, {}, b"secret", adapters=adapters,
            source_audit=lambda *a: {"raw_files": 1, "processed_files": 1},
        )
