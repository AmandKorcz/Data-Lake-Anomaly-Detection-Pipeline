from copy import deepcopy

import pandas as pd
import pytest

from src.review.ke24_review_context import build_review_context, CONTEXT_COLUMNS

A = "ALT_" + "a" * 32
B = "ALT_" + "b" * 32
COMPANY = "TKN_" + "c" * 32
COMPANY_2 = "TKN_" + "d" * 32


def data():
    return pd.DataFrame([{
        "alert_id": A,
        "company_code": COMPANY,
        "period_key": "2026-03",
        "alert_level": "PENDING",
        "classification_status": "PENDING_BUSINESS_RULE",
        "signal_method": "training_p95",
        "reference_p95_flag": True,
        "anomaly_score": .63,
        "training_p95_threshold": .55,
        "score_margin": .08,
        "dominant_measure": "revenue",
        "alert_reason": "classification_rules_not_approved",
        "reference_document": "TKN_" + "e" * 32,
        "total_absolute_value": 123456.0,
    }])


def test_export_is_readonly_and_shows_safe_context():
    original = data()
    before = original.copy(deep=True)
    result = build_review_context(original, {COMPANY: "Empresa Exemplo A"})
    pd.testing.assert_frame_equal(original, before)
    assert tuple(result.columns) == CONTEXT_COLUMNS
    assert result.loc[0, "company_label"] == "Empresa Exemplo A"
    assert result.loc[0, "period_key"] == "2026-03"
    assert result.loc[0, "dominant_measure"] == "revenue"
    assert "erro financeiro nao confirmado" in result.loc[0, "review_guidance"]
    assert "reference_document" not in result.columns
    assert "total_absolute_value" not in result.columns
    assert "TKN_" not in str(result)


def test_missing_alias_is_blocked():
    with pytest.raises(ValueError, match="alias"):
        build_review_context(data(), {})


def test_same_alias_for_different_companies_is_blocked():
    with pytest.raises(ValueError, match="duplicado"):
        build_review_context(data(), {COMPANY: "Empresa A", COMPANY_2: "Empresa A"})


@pytest.mark.parametrize("alias", ['=HYPERLINK("x")', 'a', 'X'*81, 'Empresa\nXYZ'])
def test_injected_alias_is_blocked(alias):
    with pytest.raises(ValueError):
        build_review_context(data(), {COMPANY: alias})


def test_cannot_call_alert_financial_error():
    enriched = build_review_context(data(), {COMPANY: "Empresa Exemplo A"})
    assert "anomalia estatistica" in enriched.loc[0, "signal_description"]
    assert "erro confirmado" not in enriched.loc[0, "signal_description"]


def test_source_cannot_be_unprotected():
    frame = data()
    frame.loc[0, "company_code"] = "REAL_COMPANY_CODE"
    with pytest.raises(ValueError):
        build_review_context(frame, {COMPANY: "Empresa Exemplo A"})


def test_bad_score_is_blocked():
    frame = data()
    frame.loc[0, "score_margin"] = -9
    with pytest.raises(ValueError):
        build_review_context(frame, {COMPANY: "Empresa Exemplo A"})


def test_duplicate_alert_blocked():
    frame = pd.concat([data(), data()], ignore_index=True)
    with pytest.raises(ValueError, match="duplicada"):
        build_review_context(frame, {COMPANY: "Empresa Exemplo A"})


def test_empty_frame_has_fixed_columns():
    result = build_review_context(data().iloc[:0], {})
    assert result.empty and tuple(result.columns) == CONTEXT_COLUMNS
