from copy import deepcopy
import pandas as pd
import pytest

from src.review.ke24_review_context import CONTEXT_COLUMNS, CONTEXT_HEADERS, build_review_context
from src.review.ke24_review_context_sync import plan_context_sync, validate_context_matrix
from src.review.ke24_review_sheet import build_review_sheet_matrix

A = 'ALT_' + 'a'*32
B = 'ALT_' + 'b'*32
TKN = 'TKN_' + 'c'*32
ALIASES = {TKN: 'Empresa Alfa'}


def records(ids=(A,B)):
    return pd.DataFrame([{
        'alert_id': a, 'company_code': TKN, 'period_key': '2026-03',
        'alert_level': 'PENDING', 'classification_status': 'PENDING_BUSINESS_RULE',
        'signal_method': 'training_p95', 'reference_p95_flag': True,
        'anomaly_score': .68, 'training_p95_threshold': .6, 'score_margin': .08,
        'dominant_measure': 'net_revenue',
        'alert_reason': 'classification_rules_not_ready',
        'reference_document': 'TKN_'+'d'*32, 'total_absolute_value': 199.0,
    } for a in ids])


def queue(ids=(A,B)):
    return build_review_sheet_matrix(records(ids), [])


def desired():
    context = build_review_context(records(), ALIASES)
    return [list(CONTEXT_HEADERS), *context.values.tolist()]


def test_first_context_plan_append_only_safe():
    plan = plan_context_sync(records(), ALIASES, queue())
    assert plan.existing_count == 0 and plan.new_count == 2
    assert plan.new_rows[0][1] == 'Empresa Alfa'
    assert plan.new_rows[0][4] == 'classification_rules_not_ready'
    assert 'TKN_' not in str(plan) and '199.0' not in str(plan)


def test_second_plan_idempotent():
    prior = desired()
    plan = plan_context_sync(records(), ALIASES, queue(), prior)
    assert (plan.existing_count, plan.new_count, plan.new_rows) == (2, 0, ())


def test_reordered_context_by_id_is_respected():
    prior = desired()
    prior[1], prior[2] = prior[2], prior[1]
    plan = plan_context_sync(records(), ALIASES, queue(), prior)
    assert plan.new_count == 0


def test_missing_one_context_row_appends_only_missing():
    prior = desired()[:-1]
    plan = plan_context_sync(records(), ALIASES, queue(), prior)
    assert plan.existing_count == 1 and plan.new_count == 1
    assert plan.new_rows[0][0] == B


def test_existing_context_never_rewritten():
    prior = desired()
    prior[1][1] = 'Empresa Alterada'
    with pytest.raises(RuntimeError, match='sobrescrever'):
        plan_context_sync(records(), ALIASES, queue(), prior)


def test_new_alias_mapping_does_not_rewrite_context():
    with pytest.raises(RuntimeError, match='sobrescrever'):
        plan_context_sync(records(), {TKN:'Empresa Nova'}, queue(), desired())


def test_history_queue_disagreement_blocks():
    with pytest.raises(RuntimeError, match='Fila e historico'):
        plan_context_sync(records((A,)), ALIASES, queue())


def test_extra_context_row_blocks():
    extra = desired()
    extra[2][0] = 'ALT_'+'e'*32
    with pytest.raises(RuntimeError, match='ID nao presente'):
        plan_context_sync(records(), ALIASES, queue(), extra)


@pytest.mark.parametrize('column,value', [
    (0, 'REAL_DOC'), (1, '=HYPERLINK("x")'), (2,'2026-13'),
    (4, '=IMPORTDATA("x")'), (6,'=SUM(1,2)'), (7,'URGENT'),
    (8,'REAL_STATUS'),
])
def test_bad_remote_context_rejected(column,value):
    previous = desired()
    previous[1][column] = value
    with pytest.raises((RuntimeError,ValueError)):
        plan_context_sync(records(), ALIASES, queue(), previous)


def test_private_context_never_includes_sensitive_values():
    frame = records()
    plan = plan_context_sync(frame, ALIASES, queue())
    assert 'reference_document' not in CONTEXT_COLUMNS
    assert 'total_absolute_value' not in CONTEXT_COLUMNS
    assert 'TKN_' not in str(plan)
    assert '199' not in str(plan)


def test_header_only_is_allowed():
    assert validate_context_matrix([list(CONTEXT_HEADERS)]) == {}


def test_inputs_unmodified():
    source=records(); baseline=queue(); before=source.copy(deep=True); b=deepcopy(baseline)
    plan_context_sync(source, ALIASES, baseline)
    pd.testing.assert_frame_equal(source,before)
    assert baseline==b
