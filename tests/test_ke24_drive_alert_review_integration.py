from src.pipeline import run_ke24_drive_alert_persistence as persistence
from test_ke24_drive_alert_persistence import make_history, privacy_policy


def test_reviewed_alert_is_not_represented_as_new_pending_alert():
    previous = make_history((2,))
    current = make_history((2, 3))
    reviewed = previous.loc[0, "alert_id"]
    original = previous.copy(deep=True)

    merged, new_count, suppressed = persistence.prepare_alerts_respecting_reviews(
        current, previous, {reviewed: "NOT_AN_ERROR"}, privacy_policy()
    )

    assert suppressed == 1
    assert new_count == 1
    assert len(merged) == 2
    assert merged["alert_id"].is_unique
    assert merged.loc[merged["alert_id"].eq(reviewed), "detected_at_utc"].iloc[0] == (
        original.loc[0, "detected_at_utc"]
    )
    assert merged.loc[merged["alert_id"].eq(reviewed), "investigation_status"].iloc[0] == (
        original.loc[0, "investigation_status"]
    )


def test_pending_alert_can_be_reencountered_without_duplicate_publication():
    previous = make_history((2,))
    current = make_history((2,))
    aid = previous.loc[0, "alert_id"]
    merged, new_count, suppressed = persistence.prepare_alerts_respecting_reviews(
        current, previous, {aid: "PENDING"}, privacy_policy()
    )
    assert suppressed == 0
    assert new_count == 0
    assert len(merged) == 1


def test_no_removed_history_when_all_current_candidates_are_handled():
    previous = make_history((2, 3))
    current = make_history((2,))
    aid = current.loc[0, "alert_id"]
    merged, new_count, suppressed = persistence.prepare_alerts_respecting_reviews(
        current, previous, {aid: "CORRECTED"}, privacy_policy()
    )
    assert suppressed == 1 and new_count == 0
    assert set(merged["alert_id"]) == set(previous["alert_id"])
