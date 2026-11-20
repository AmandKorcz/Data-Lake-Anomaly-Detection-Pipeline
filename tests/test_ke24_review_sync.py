from copy import deepcopy

import pandas as pd
import pytest

from src.review import ke24_review_sheet as sh
from src.review import ke24_review_sync as sync
from src.review import ke24_review_workflow as rw

A = "ALT_" + "a" * 32
B = "ALT_" + "b" * 32
C = "ALT_" + "c" * 32
ACTOR = "REV_" + "f" * 32


def alerts(ids=(A, B)):
    return pd.DataFrame({
        "alert_id": list(ids),
        "period_key": ["2026-11"] * len(ids),
        "alert_level": ["PENDING"] * len(ids),
        "classification_status": ["PENDING_BUSINESS_RULE"] * len(ids),
    })


def snapshot(ids=(A,)):
    return sh.build_review_sheet_matrix(alerts(ids), [])


def edit_rows(matrix, index, value):
    data = deepcopy(matrix)
    data[index][-1] = value
    return data


def test_first_publication_plan_never_authorizes_write():
    plan = sync.plan_review_sheet_sync(alerts(), [])
    assert plan.new_alert_count == 2
    assert plan.pending_request_count == 0
    assert plan.publication_authorized is False
    assert plan.desired_matrix[1][-1] == "PENDING"


def test_first_publication_cannot_hide_existing_snapshot():
    with pytest.raises(ValueError):
        sync.plan_review_sheet_sync(alerts(), [], [], trusted_snapshot=snapshot())


def test_existing_sheet_requires_trusted_snapshot():
    with pytest.raises(ValueError, match="Snapshot"):
        sync.plan_review_sheet_sync(alerts(), [], snapshot())


def test_existing_sheet_no_changes_is_idempotent():
    state = snapshot((A, B))
    plan = sync.plan_review_sheet_sync(alerts(), [], deepcopy(state), trusted_snapshot=state)
    assert plan.new_alert_count == plan.pending_request_count == 0
    assert plan.desired_matrix == state


def test_pending_request_preserved_when_new_alert_arrives():
    prior = snapshot()
    changed = edit_rows(prior, 1, "IN_REVIEW")
    plan = sync.plan_review_sheet_sync(alerts((A, B)), [], changed, trusted_snapshot=prior)
    assert plan.new_alert_count == 1
    assert plan.pending_request_count == 1
    assert plan.pending_requests == ((A, "IN_REVIEW", 0),)
    assert plan.desired_matrix[1][-1] == "IN_REVIEW"
    assert plan.desired_matrix[2][-1] == "PENDING"
    assert plan.desired_matrix[1][4] == "PENDING"  # nao confundir pedido com estado verificado
    assert plan.publication_authorized is False


def test_sheet_reorder_preserves_requested_value_by_alert_id():
    initial = snapshot((A, B))
    changed = deepcopy(initial)
    changed.reverse()
    changed = [initial[0], changed[0], changed[1]]
    changed[1][-1] = "IN_REVIEW"  # B
    plan = sync.plan_review_sheet_sync(alerts((A, B, C)), [], changed, trusted_snapshot=initial)
    result = {r[0]: r for r in plan.desired_matrix[1:]}
    assert result[B][-1] == "IN_REVIEW"
    assert result[A][-1] == "PENDING"


def test_editing_immutable_cell_blocks():
    state = snapshot()
    changed = deepcopy(state)
    changed[1][2] = "HIGH"
    with pytest.raises(ValueError, match="Campo tecnico"):
        sync.plan_review_sheet_sync(alerts((A,)), [], changed, trusted_snapshot=state)


@pytest.mark.parametrize("column", range(7))
def test_each_immutable_column_blocks(column):
    state = snapshot()
    changed = deepcopy(state)
    if column == 0:
        changed[1][0] = B
    elif column in (1, 2, 3, 4):
        changed[1][column] = {1:"2026-10",2:"HIGH",3:"PENDING_TECHNICAL_RULE",4:"IN_REVIEW"}[column]
    elif column == 5:
        changed[1][5] = 1
        changed[1][4] = "IN_REVIEW"
        changed[1][6] = "2026-11-18T10:00:00Z"
    else:
        changed[1][6] = "2026-11-18T10:00:00Z"
        changed[1][5] = 1
        changed[1][4] = "IN_REVIEW"
    with pytest.raises(ValueError):
        sync.plan_review_sheet_sync(alerts((A,)), [], changed, trusted_snapshot=state)


def test_deleting_alert_blocks():
    state = snapshot((A, B))
    changed = state[:-1]
    with pytest.raises(ValueError, match="removidos"):
        sync.plan_review_sheet_sync(alerts((A, B)), [], changed, trusted_snapshot=state)


def test_unknown_alert_added_manually_blocks():
    state = snapshot((A,))
    another = snapshot((B,))[1]
    changed = deepcopy(state) + [another]
    with pytest.raises(ValueError, match="inseridos"):
        sync.plan_review_sheet_sync(alerts((A, B)), [], changed, trusted_snapshot=state)


def test_source_deletes_old_alert_blocks():
    state = snapshot((A, B))
    with pytest.raises(ValueError, match="historico ausente"):
        sync.plan_review_sheet_sync(alerts((A,)), [], state, trusted_snapshot=state)


def test_change_to_technical_origin_blocks():
    state = snapshot((A,))
    changed = alerts((A,))
    changed.loc[0, "period_key"] = "2026-10"
    with pytest.raises(ValueError, match="Origem historica"):
        sync.plan_review_sheet_sync(changed, [], state, trusted_snapshot=state)


@pytest.mark.parametrize("status", ["IN_REVIEW", "CONFIRMED_ERROR", "CORRECTED", "NOT_AN_ERROR"])
def test_pending_can_go_directly_to_any_other_status(status):
    state = snapshot()
    changed = edit_rows(state, 1, status)
    plan = sync.plan_review_sheet_sync(alerts((A,)), [], changed, trusted_snapshot=state)
    assert plan.pending_requests == ((A, status, 0),)
    assert plan.desired_matrix[1][-1] == status


def test_invalid_formula_blocks():
    state = snapshot()
    changed = edit_rows(state, 1, "=IMPORTXML(x)")
    with pytest.raises(ValueError):
        sync.plan_review_sheet_sync(alerts((A,)), [], changed, trusted_snapshot=state)


def test_verified_event_updates_status_if_no_user_edit():
    state = snapshot()
    events = rw.append_review_event([], alerts((A,)), alert_id=A,
                                    new_status="IN_REVIEW", expected_revision=0,
                                    reviewer_token=ACTOR)
    plan = sync.plan_review_sheet_sync(alerts((A,)), events, state, trusted_snapshot=state)
    assert plan.pending_request_count == 0
    assert plan.desired_matrix[1][4] == "IN_REVIEW"
    assert plan.desired_matrix[1][-1] == "IN_REVIEW"
    assert plan.desired_matrix[1][5] == 1


def test_verified_event_with_concurrent_user_edit_blocks():
    state = snapshot()
    changed = edit_rows(state, 1, "IN_REVIEW")
    events = rw.append_review_event([], alerts((A,)), alert_id=A,
                                    new_status="IN_REVIEW", expected_revision=0,
                                    reviewer_token=ACTOR)
    with pytest.raises(ValueError, match="concorrente"):
        sync.plan_review_sheet_sync(alerts((A,)), events, changed, trusted_snapshot=state)


def test_previously_unresolved_request_with_new_event_blocks():
    base = edit_rows(snapshot(), 1, "IN_REVIEW")
    events = rw.append_review_event([], alerts((A,)), alert_id=A,
                                    new_status="IN_REVIEW", expected_revision=0,
                                    reviewer_token=ACTOR)
    with pytest.raises(ValueError, match="concorrente"):
        sync.plan_review_sheet_sync(alerts((A,)), events, base, trusted_snapshot=base)


def test_previous_request_survives_idempotent_sync():
    state = edit_rows(snapshot(), 1, "IN_REVIEW")
    plan = sync.plan_review_sheet_sync(alerts((A, B)), [], state, trusted_snapshot=state)
    assert plan.pending_request_count == 1
    assert plan.desired_matrix[1][-1] == "IN_REVIEW"


def test_input_matrices_not_mutated():
    state = snapshot()
    observed = edit_rows(state, 1, "IN_REVIEW")
    old = deepcopy(observed)
    expected = deepcopy(state)
    sync.plan_review_sheet_sync(alerts((A, B)), [], observed, trusted_snapshot=state)
    assert observed == old and state == expected


def test_demo_is_offline_and_counts_only(capsys):
    assert sync.main(["--demo"]) == 0
    result = capsys.readouterr().out
    assert "Publicacao autorizada: False" in result
    assert "Novos alertas: 1" in result
    assert "ALT_" not in result
    assert "REV_" not in result
