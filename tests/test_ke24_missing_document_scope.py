import pandas as pd
import pytest
from src.review.ke24_review_recurrence import plan_business_recurrences


def tok(char):
    return "TKN_" + char * 32


def row(char, *, company="a", document="b", period="2026-02", amount=100,
        measure="revenue", source="e", line=2):
    return {
        "alert_id": "ALT_" + char * 32,
        "company_code": tok(company),
        "reference_document": tok(document) if document else None,
        "period_key": period,
        "profit_center": tok("c"),
        "product": tok("d"),
        "currency": "BRL",
        "currency_type": 10,
        "dominant_measure": measure,
        "signal_method": "training_p95",
        "total_absolute_value": amount,
        "dominant_measure_value": amount,
        "_source_file_sha256": tok(source),
        "_source_row_number": line,
    }


def plan(previous, current):
    a = pd.DataFrame(previous)
    b = pd.DataFrame(current)
    return plan_business_recurrences(a, b, {r["alert_id"]: "CORRECTED" for r in previous})


def test_missing_document_in_another_period_is_new_and_publishable():
    p = plan([row("1", period="2025-12")], [row("2", document=None, period="2026-03")])
    assert len(p.eligible) == 1
    assert p.unmatchable_candidates == 1
    assert p.safe_unmatchable_candidates == 1
    assert p.publication_blockers == 0
    assert p.suppressed_reviewed == 0


def test_missing_document_in_another_company_is_new_and_publishable():
    p = plan([row("1", company="a")], [row("2", company="f", document=None)])
    assert p.publication_blockers == 0
    assert p.safe_unmatchable_candidates == 1


def test_missing_document_with_previous_in_same_scope_is_blocked():
    p = plan([row("1")], [row("2", document=None)])
    assert p.unmatchable_candidates == 1
    assert p.safe_unmatchable_candidates == 0
    assert p.publication_blockers == 1


def test_missing_document_with_unknown_historical_record_in_scope_is_blocked():
    p = plan([row("1", document=None)], [row("2", document=None)])
    assert p.publication_blockers == 1


def test_same_partial_identity_inside_current_batch_is_blocked():
    p = plan([row("1", period="2025-12")], [
        row("2", document=None, period="2026-03", source="f"),
        row("3", document=None, period="2026-03", source="9"),
    ])
    assert len(p.eligible) == 2
    assert p.safe_unmatchable_candidates == 0
    assert p.publication_blockers == 2


def test_missing_document_collides_with_documented_current_candidate_is_blocked():
    p = plan([row("1", period="2025-12")], [
        row("2", document=None, period="2026-03", source="f"),
        row("3", document="6", period="2026-03", source="9"),
    ])
    assert p.safe_unmatchable_candidates == 0
    assert p.publication_blockers == 1


def test_two_distinct_missing_document_events_may_be_new():
    p = plan([row("1", period="2025-12")], [
        row("2", document=None, period="2026-03", amount=100),
        row("3", document=None, period="2026-03", amount=200),
    ])
    assert p.unmatchable_candidates == 2
    assert p.safe_unmatchable_candidates == 2
    assert p.publication_blockers == 0
    assert len(p.eligible) == 2


def test_missing_document_and_missing_measure_still_blocks():
    p = plan([row("1", period="2025-12")], [
        row("2", document=None, period="2026-03", measure=""),
    ])
    assert p.unmatchable_candidates == 1
    assert p.safe_unmatchable_candidates == 0
    assert p.publication_blockers == 1


def test_exact_id_still_keeps_historical_status_when_document_missing():
    original = row("1", period="2025-12")
    new = dict(original, reference_document=None)
    p = plan([original], [new])
    assert p.suppressed_reviewed == 1
    assert p.publication_blockers == 0
    assert p.unmatchable_candidates == 0


def test_unprotected_document_is_rejected():
    with pytest.raises(ValueError, match="protecao"):
        plan([row("1")], [row("2", document=None) | {"reference_document": "raw document"}])
