from datetime import datetime, timezone

import pandas as pd
import pytest

from src.review import ke24_review_workflow as rw

A = "ALT_" + "a" * 32
B = "ALT_" + "b" * 32
C = "ALT_" + "c" * 32
ACTOR = "REV_" + "f" * 32
WHEN = datetime(2026, 11, 17, 20, 0, tzinfo=timezone.utc)


def alerts(ids=(A, B)):
    return pd.DataFrame({
        "alert_id": list(ids), "investigation_status": ["DETECTED"] * len(ids),
        "classification_status": ["PENDING_BUSINESS_RULE"] * len(ids),
    })


def add(events, incoming=None, *, alert_id=A, status=rw.IN_REVIEW, expected=0, who=ACTOR, when=WHEN):
    return rw.append_review_event(
        events, alerts() if incoming is None else incoming,
        alert_id=alert_id, new_status=status, expected_revision=expected,
        reviewer_token=who, reviewed_at=when,
    )


def test_empty_events_default_pending():
    result = rw.build_review_projection(alerts(), [])
    assert result.review_status.tolist() == ["PENDING", "PENDING"]
    assert result.review_revision.tolist() == [0, 0]
    assert result.review_updated_at_utc.isna().all()


def test_review_does_not_replace_technical_status():
    source = alerts()
    source_copy = source.copy(deep=True)
    result = rw.build_review_projection(source, add([]))
    assert result.loc[0, "review_status"] == "IN_REVIEW"
    assert result.investigation_status.tolist() == ["DETECTED", "DETECTED"]
    assert result.classification_status.tolist() == ["PENDING_BUSINESS_RULE"] * 2
    pd.testing.assert_frame_equal(source, source_copy)


def test_event_is_append_only_not_mutating_source():
    events = []
    updated = add(events)
    assert events == []
    assert len(updated) == 1
    assert updated[0]["event_id"].startswith("REV_EVT_")
    assert updated[0]["revision"] == 1
    assert updated[0]["reason"] == "START_REVIEW"


def test_complete_correction_path():
    events = add([])
    events = add(events, status=rw.CONFIRMED_ERROR, expected=1)
    events = add(events, status=rw.CORRECTED, expected=2)
    assert rw.build_review_projection(alerts(), events).review_status.tolist() == ["CORRECTED", "PENDING"]
    assert [x["revision"] for x in events] == [1, 2, 3]


def test_false_positive_path():
    events = add([])
    events = add(events, status=rw.NOT_AN_ERROR, expected=1)
    assert rw.build_review_projection(alerts(), events).loc[0, "review_status"] == "NOT_AN_ERROR"


def test_reopen_after_correction():
    events = add([])
    events = add(events, status=rw.CONFIRMED_ERROR, expected=1)
    events = add(events, status=rw.CORRECTED, expected=2)
    events = add(events, status=rw.IN_REVIEW, expected=3)
    assert events[-1]["reason"] == "REOPENED"


def test_reopen_after_false_positive():
    events = add(add([], status=rw.IN_REVIEW), status=rw.NOT_AN_ERROR, expected=1)
    assert add(events, status=rw.IN_REVIEW, expected=2)[-1]["reason"] == "REOPENED"


def test_return_pending_for_more_information():
    events = add([])
    result = add(events, status=rw.PENDING, expected=1)
    assert result[-1]["reason"] == "NEED_MORE_INFORMATION"


def test_existing_review_survives_next_batch():
    old = alerts((A,))
    events = add([], old)
    new = alerts((A, B, C))
    projected = rw.build_review_projection(new, events)
    assert projected.review_status.tolist() == ["IN_REVIEW", "PENDING", "PENDING"]


def test_order_of_alerts_does_not_change_review():
    events = add([])
    projected = rw.build_review_projection(alerts((B, A)), events)
    assert projected.review_status.tolist() == ["PENDING", "IN_REVIEW"]


def test_stale_revision_conflict():
    events = add([])
    with pytest.raises(rw.ReviewConflict):
        add(events, status=rw.CONFIRMED_ERROR, expected=0)


@pytest.mark.parametrize("new_status", [rw.CORRECTED, rw.CONFIRMED_ERROR, rw.NOT_AN_ERROR, "REJECTED", "", None])
def test_cannot_skip_review_or_use_unsupported_status(new_status):
    with pytest.raises(ValueError, match="Transicao"):
        add([], status=new_status)


def test_cannot_skip_from_confirmed_error_to_not_an_error():
    events = add(add([]), status=rw.CONFIRMED_ERROR, expected=1)
    with pytest.raises(ValueError, match="Transicao"):
        add(events, status=rw.NOT_AN_ERROR, expected=2)


def test_invalid_reviewer_rejected():
    with pytest.raises(ValueError, match="Revisor"):
        add([], who="amanda@example.com")


def test_unknown_alert_rejected():
    with pytest.raises(ValueError, match="autorizado"):
        add([], alert_id=C)


def test_bad_alert_id_rejected():
    with pytest.raises(ValueError, match="ID de alerta"):
        add([], alert_id="REAL_COMPANY")


def test_duplicate_alert_ids_block():
    with pytest.raises(ValueError, match="duplicados"):
        rw.build_review_projection(alerts((A, A)), [])


def test_unknown_event_blocks_instead_of_dropping_decision():
    events = add([])
    with pytest.raises(ValueError, match="ausente"):
        rw.build_review_projection(alerts((B,)), events)


def test_tampered_previous_status_blocks():
    events = add([])
    events[0]["previous_status"] = "CORRECTED"
    with pytest.raises(ValueError, match="Transicao"):
        rw.build_review_projection(alerts(), events)


def test_tampered_reason_blocks():
    events = add([])
    events[0]["reason"] = "CORRECTION_VERIFIED"
    with pytest.raises(ValueError, match="Transicao"):
        rw.build_review_projection(alerts(), events)


def test_tampered_revision_blocks():
    events = add([])
    events[0]["revision"] = 8
    with pytest.raises(ValueError, match="Revisao"):
        rw.build_review_projection(alerts(), events)


def test_duplicate_event_id_blocks():
    events = add([])
    later = add(events, status=rw.CONFIRMED_ERROR, expected=1)
    later[1]["event_id"] = later[0]["event_id"]
    with pytest.raises(ValueError, match="duplicado"):
        rw.build_review_projection(alerts(), later)


def test_extra_columns_in_event_block():
    events = add([])
    events[0]["company_code"] = "REAL_PRIVATE_ID"
    with pytest.raises(ValueError, match="Contrato"):
        rw.build_review_projection(alerts(), events)


def test_naive_datetime_block():
    with pytest.raises(ValueError, match="Data"):
        add([], when=datetime(2026, 11, 17, 20, 0))


def test_invalid_persisted_timestamp_block():
    events = add([])
    events[0]["reviewed_at_utc"] = "today"
    with pytest.raises(ValueError, match="Horario"):
        rw.build_review_projection(alerts(), events)


def test_out_of_order_timestamp_block():
    events = add([])
    later = add(events, status=rw.CONFIRMED_ERROR, expected=1)
    later[1]["reviewed_at_utc"] = "2026-11-16T20:00:00Z"
    with pytest.raises(ValueError, match="temporal"):
        rw.build_review_projection(alerts(), later)


def test_never_overwrite_existing_review_columns():
    frame = alerts()
    frame["review_status"] = ["CORRECTED", "PENDING"]
    with pytest.raises(ValueError, match="ja existe"):
        rw.build_review_projection(frame, [])


def test_only_counts_summary():
    events = add([])
    summary = rw.summarize_review_status(rw.build_review_projection(alerts(), events))
    assert summary == {"PENDING": 1, "IN_REVIEW": 1, "CONFIRMED_ERROR": 0, "CORRECTED": 0, "NOT_AN_ERROR": 0}
    assert A not in str(summary)


def test_invalid_summary_block():
    data = pd.DataFrame({"review_status": ["CORRECTED", "UNKNOWN"]})
    with pytest.raises(ValueError):
        rw.summarize_review_status(data)


def test_demo_is_offline_and_leaks_no_ids(capsys):
    assert rw.main(["--demo"]) == 0
    out = capsys.readouterr().out
    assert "Publicacao autorizada: False" in out
    assert "ALT_" not in out and "REV_" not in out


def test_empty_alerts_with_no_events_allowed():
    result = rw.build_review_projection(alerts(()), [])
    assert result.empty
    assert rw.summarize_review_status(result)["PENDING"] == 0


def test_empty_alerts_with_orphan_event_blocked():
    with pytest.raises(ValueError, match="ausente"):
        rw.build_review_projection(alerts(()), add([]))
