import pandas as pd
import pytest

from src.review.ke24_review_recurrence import plan_business_recurrences


def tok(c):
    return 'TKN_' + c * 32


def aid(c):
    return 'ALT_' + c * 32


def row(c='1', *, company='a', document='b', period='2026-03',
        profit='c', product='d', currency='BRL', measure='revenue',
        source='e', line=2, amount=100.0):
    return dict(
        alert_id=aid(c), company_code=tok(company),
        reference_document=None if document is None else tok(document),
        period_key=period,
        profit_center=None if profit is None else tok(profit),
        product=None if product is None else tok(product),
        currency=currency, currency_type=10, dominant_measure=measure,
        signal_method='training_p95',
        _source_file_sha256=tok(source), _source_row_number=line,
        total_absolute_value=amount, dominant_measure_value=amount,
    )


def plan(old, current, status='CORRECTED'):
    old = pd.DataFrame(old) if old else pd.DataFrame(columns=list(row()))
    current = pd.DataFrame(current) if current else pd.DataFrame(columns=list(row()))
    statuses = {r['alert_id']: status for r in old.to_dict('records')}
    return plan_business_recurrences(old, current, statuses)


def test_match_between_distinct_source_files_without_changing_original_id():
    previous = row('1', source='e', line=2)
    new = row('2', source='f', line=29)
    before = pd.DataFrame([previous])
    candidates = pd.DataFrame([new])
    result = plan_business_recurrences(before, candidates, {aid('1'): 'CORRECTED'})
    assert result.eligible.empty
    assert result.matched_business == 1
    assert result.suppressed_reviewed == 1
    assert result.publication_blockers == 0
    assert before.iloc[0]['alert_id'] == aid('1')
    assert candidates.iloc[0]['alert_id'] == aid('2')


@pytest.mark.parametrize('status', ['IN_REVIEW', 'CONFIRMED_ERROR', 'CORRECTED', 'NOT_AN_ERROR'])
def test_all_nonpending_statuses_are_treated(status):
    result = plan([row('1')], [row('2', source='f')], status=status)
    assert result.eligible.empty and result.suppressed_reviewed == 1


def test_same_business_pending_is_not_republished_or_reclassified():
    result = plan([row('1')], [row('2', source='f')], status='PENDING')
    assert result.eligible.empty and result.duplicates_pending == 1
    assert result.suppressed_reviewed == 0


def test_same_id_has_priority_without_untrusted_doc():
    result = plan([row('1')], [row('1', document=None)], status='CORRECTED')
    assert result.matched_exact_id == 1
    assert result.unmatchable_candidates == 0
    assert result.eligible.empty


@pytest.mark.parametrize('change', [
    {'company': 'f'}, {'document': 'a'}, {'period': '2026-04'},
    {'profit': 'a'}, {'product': 'a'}, {'currency': 'USD'},
    {'measure': 'cost_of_goods'}
])
def test_distinct_business_criteria_do_not_merge(change):
    result = plan([row('1')], [row('2', source='f', **change)])
    assert len(result.eligible) == 1
    assert result.matched_business == 0
    assert result.suppressed_reviewed == 0


def test_financial_amount_change_is_new_signal_not_silently_suppressed():
    result = plan([row('1', amount=100.0)], [row('2', amount=250.0)])
    assert len(result.eligible) == 1 and result.matched_business == 0


def test_old_duplicate_business_key_blocks_automatic_merging():
    result = plan([row('1'), row('2')], [row('3', source='f')])
    assert result.ambiguous_candidates == 1
    assert result.publication_blockers == 1
    assert len(result.eligible) == 1


def test_new_duplicate_business_key_blocks_automatic_merging():
    result = plan([row('1')], [row('2', source='f'), row('3', source='a')])
    assert result.ambiguous_candidates == 2
    assert result.publication_blockers == 2
    assert len(result.eligible) == 2


def test_missing_document_requires_review_before_publication():
    result = plan([row('1')], [row('2', document=None)])
    assert result.unmatchable_candidates == 1
    assert result.publication_blockers == 1


def test_first_run_does_not_require_business_document():
    result = plan([], [row('2', document=None)])
    assert len(result.eligible) == 1
    assert result.publication_blockers == 0


def test_unprotected_company_or_document_is_rejected():
    with pytest.raises(ValueError, match='protecao'):
        plan([row('1')], [dict(row('2'), company_code='original')])
    with pytest.raises(ValueError, match='protecao'):
        plan([row('1')], [dict(row('2'), reference_document='12345')])


def test_missing_old_status_is_blocked():
    with pytest.raises(ValueError, match='integralmente'):
        plan_business_recurrences(pd.DataFrame([row('1')]), pd.DataFrame([row('2')]), {})


def test_duplicate_ids_blocked():
    with pytest.raises(ValueError, match='duplicados'):
        plan([row('1')], [row('2'), row('2')])


def test_separate_document_in_same_company_is_a_new_alert():
    result = plan([row('1')], [row('2', document='f')])
    assert len(result.eligible) == 1 and result.publication_blockers == 0


def test_no_history_or_candidates():
    result = plan([], [])
    assert result.eligible.empty and result.publication_blockers == 0


def test_float_and_int_equivalent_amounts_are_matched():
    result = plan([row('1', amount=100)], [row('2', amount=100.0)])
    assert result.matched_business == 1


def test_change_in_dominant_value_is_not_ignored():
    previous = row('1')
    current = dict(row('2'), dominant_measure_value=105.0)
    result = plan([previous], [current])
    assert len(result.eligible) == 1


def test_legacy_history_missing_reference_blocks_new_comparable_candidate():
    result = plan([row('1', document=None)], [row('2', document='b')])
    assert result.ambiguous_candidates == 1
    assert result.publication_blockers == 1
    assert len(result.eligible) == 1


def test_unknown_history_in_other_period_does_not_block_unrelated_new_event():
    result = plan([row('1', document=None, period='2025-12')],
                  [row('2', document='b', period='2026-03')])
    assert result.publication_blockers == 0
    assert len(result.eligible) == 1
