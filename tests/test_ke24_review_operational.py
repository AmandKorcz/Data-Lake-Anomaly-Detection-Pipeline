from copy import deepcopy
import pandas as pd
import pytest

from src.review.ke24_review_sheet import build_review_sheet_matrix
from src.review.ke24_review_operational import (
    operational_status_map, suppress_reviewed_recurrences, summarize_statuses,
)

A = "ALT_" + "a" * 32
B = "ALT_" + "b" * 32
C = "ALT_" + "c" * 32


def history(ids=(A, B)):
    return pd.DataFrame({
        "alert_id": list(ids),
        "period_key": ["2026-11"] * len(ids),
        "alert_level": ["PENDING"] * len(ids),
        "classification_status": ["PENDING_BUSINESS_RULE"] * len(ids),
    })


def baseline():
    return build_review_sheet_matrix(history(), [])


@pytest.mark.parametrize("choice", [
    "IN_REVIEW", "CONFIRMED_ERROR", "CORRECTED", "NOT_AN_ERROR"
])
def test_any_nonpending_status_suppresses_same_alert_id(choice):
    original = baseline()
    observed = deepcopy(original)
    observed[1][-1] = choice
    statuses = operational_status_map(history(), observed, trusted_snapshot=original)
    frame = pd.DataFrame({"alert_id": [A, B, C], "alert_level": ["PENDING"] * 3})
    before = frame.copy(deep=True)
    eligible, n = suppress_reviewed_recurrences(frame, statuses)
    assert eligible["alert_id"].tolist() == [B, C]
    assert n == 1
    assert statuses[A] == choice
    pd.testing.assert_frame_equal(frame, before)


def test_pending_in_sheet_stays_eligible():
    statuses = operational_status_map(history(), baseline(), trusted_snapshot=baseline())
    eligible, n = suppress_reviewed_recurrences(history(), statuses)
    assert n == 0 and len(eligible) == 2


def test_reset_to_pending_is_immediately_respected():
    old = baseline()
    old[1][-1] = "CORRECTED"
    new = deepcopy(old)
    new[1][-1] = "PENDING"
    statuses = operational_status_map(history(), new, trusted_snapshot=old)
    eligible, n = suppress_reviewed_recurrences(history(), statuses)
    assert n == 0 and len(eligible) == 2


def test_reorder_uses_alert_id_not_sheet_row():
    old = baseline()
    observed = [old[0], old[2].copy(), old[1].copy()]
    observed[1][-1] = "CORRECTED"  # B mudou de linha
    statuses = operational_status_map(history(), observed, trusted_snapshot=old)
    assert statuses[B] == "CORRECTED" and statuses[A] == "PENDING"


def test_history_count_must_match_sheet():
    with pytest.raises(RuntimeError, match="defasada"):
        operational_status_map(history((A, B, C)), baseline(), trusted_snapshot=baseline())


def test_deleted_sheet_row_is_blocked():
    with pytest.raises(ValueError, match="removidos"):
        operational_status_map(history(), baseline()[:-1], trusted_snapshot=baseline())


def test_tampered_protected_field_is_blocked():
    changed = deepcopy(baseline())
    changed[1][1] = "2026-10"
    with pytest.raises(ValueError, match="tecnico"):
        operational_status_map(history(), changed, trusted_snapshot=baseline())


def test_missing_trusted_snapshot_is_blocked():
    with pytest.raises(ValueError, match="assinado"):
        operational_status_map(history(), baseline(), trusted_snapshot=None)


def test_unknown_status_and_formula_are_blocked():
    for choice in ("FINISHED", "=IMPORTXML(x)"):
        changed = deepcopy(baseline())
        changed[1][-1] = choice
        with pytest.raises(ValueError):
            operational_status_map(history(), changed, trusted_snapshot=baseline())


def test_missing_or_corrupted_ids_blocked():
    with pytest.raises(ValueError):
        suppress_reviewed_recurrences(pd.DataFrame({"alert_id": ["A"]}), {})
    with pytest.raises(ValueError):
        suppress_reviewed_recurrences(history((A, A)), {})


def test_aggregate_counts_exclude_identity():
    stats = summarize_statuses({A: "CORRECTED", B: "PENDING", C: "NOT_AN_ERROR"})
    assert stats.total_history == 3
    assert stats.reviewed == 2 and stats.pending == 1
    assert stats.by_status["NOT_AN_ERROR"] == 1
