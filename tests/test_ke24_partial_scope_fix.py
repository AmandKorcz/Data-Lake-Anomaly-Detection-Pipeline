import pandas as pd
import pytest

from src.review.ke24_review_recurrence import plan_business_recurrences


def tok(c):
    return "TKN_" + c * 32


def aid(c):
    return "ALT_" + c * 32


def row(c, *, doc="b", source="e", line=2, amount=100,
        currency="RMB", measure="revenue", period="2026-03"):
    return {
        "alert_id": aid(c),
        "company_code": tok("a"),
        "reference_document": None if doc is None else tok(doc),
        "period_key": period,
        "profit_center": tok("c"),
        "product": tok("d"),
        "currency": currency,
        "currency_type": 10,
        "dominant_measure": measure,
        "signal_method": "training_p95",
        "total_absolute_value": amount,
        "dominant_measure_value": amount,
        "_source_file_sha256": tok(source) if source else None,
        "_source_row_number": line,
    }


def check(older, newer):
    history = pd.DataFrame(older)
    candidates = pd.DataFrame(newer)
    return plan_business_recurrences(
        history, candidates, {item["alert_id"]: "PENDING" for item in older}
    )


def test_same_export_distinct_row_and_distinct_partial_is_safe():
    p = check([row("1", doc=None, line=2, amount=100)],
              [row("2", doc="b", line=3, amount=250)])
    assert p.publication_blockers == 0
    assert len(p.eligible) == 1
    assert p.matched_business == 0
    assert p.suppressed_reviewed == 0


def test_identical_partial_even_different_row_stays_blocked():
    p = check([row("1", doc=None, line=2)], [row("2", doc="b", line=3)])
    assert p.publication_blockers == 1


def test_different_export_even_different_partial_stays_blocked():
    p = check([row("1", doc=None, source="e", amount=100)],
              [row("2", source="f", amount=250)])
    assert p.publication_blockers == 1


def test_same_export_same_row_stays_blocked():
    p = check([row("1", doc=None, source="e", line=2, amount=100)],
              [row("2", source="e", line=2, amount=250)])
    assert p.publication_blockers == 1


def test_unknown_source_provenance_stays_blocked():
    p = check([row("1", doc=None, source=None, line=2, amount=100)],
              [row("2", source=None, line=3, amount=250)])
    assert p.publication_blockers == 1


def test_missing_source_row_stays_blocked():
    p = check([row("1", doc=None, line=None, amount=100)],
              [row("2", line=3, amount=250)])
    assert p.publication_blockers == 1


def test_mixed_scopes_every_incomplete_row_must_be_excluded():
    p = check([row("1", doc=None, line=2, amount=100),
               row("3", doc=None, source="f", line=4, amount=120)],
              [row("2", line=3, amount=250)])
    assert p.publication_blockers == 1


def test_candidate_matching_complete_prior_key_cannot_be_released():
    p = check([row("1", doc=None, line=2, amount=100),
               row("3", doc="b", line=4, amount=250)],
              [row("2", doc="b", line=3, amount=250)])
    assert p.publication_blockers == 1


def test_different_currency_is_a_provably_distinct_group():
    p = check([row("1", doc=None, currency="BRL", source="e")],
              [row("2", doc="b", currency="RMB", source="f")])
    assert p.publication_blockers == 0
    assert len(p.eligible) == 1


def test_current_duplicate_full_key_keeps_blocked():
    p = check([row("1", doc=None, line=2, amount=100)],
              [row("2", line=3, amount=250), row("3", line=4, amount=250)])
    assert p.publication_blockers == 2


def test_equal_id_represented_once_without_publication():
    old = row("1", doc=None, line=2)
    p = check([old], [dict(old)])
    assert p.publication_blockers == 0
    assert p.duplicates_pending == 1
    assert p.eligible.empty


def test_complete_old_business_key_still_suppresses_reviewed():
    historical = pd.DataFrame([row("1", doc="b", source="e")])
    current = pd.DataFrame([row("2", doc="b", source="f", line=4)])
    p = plan_business_recurrences(historical, current, {aid("1"): "CORRECTED"})
    assert p.suppressed_reviewed == 1
    assert p.eligible.empty


def test_incomplete_non_document_old_key_still_blocks():
    p = check([row("1", doc="b", measure="invalid!", line=2)],
              [row("2", doc="b", amount=250, line=3)])
    assert p.publication_blockers == 1


def test_diagnostic_reports_released_candidates_separately():
    from tools.diagnose_ke24_ambiguities import summarize_ambiguous_recurrences
    old = pd.DataFrame([row("1", doc=None, line=2, amount=100)])
    new = pd.DataFrame([row("2", doc="b", line=3, amount=250)])
    report = summarize_ambiguous_recurrences(old, new)
    assert report["distintos_por_proveniencia"] == 1
    assert report["ambiguos_total"] == 0
    assert report["bloqueios_totais"] == 0
