from __future__ import annotations

import pandas as pd
from tools.diagnose_ke24_ambiguities import summarize_ambiguous_recurrences


def row(num, *, doc=True, amount=10, company='a', period='2026-03', measure='revenue'):
    return {
        'alert_id': 'ALT_' + format(num, '032x'),
        'company_code': 'TKN_' + ("a" if company == 'a' else 'b') * 32,
        'reference_document': 'TKN_' + 'c'*32 if doc else None,
        'period_key': period,
        'profit_center': 'TKN_' + 'd'*32,
        'product': 'TKN_' + 'e'*32,
        'currency': 'RMB',
        'currency_type': 10,
        'dominant_measure': measure,
        'signal_method': 'training_p95',
        'total_absolute_value': amount,
        'dominant_measure_value': 8,
    }


def df(*rows):
    if rows:
        return pd.DataFrame(rows)
    return pd.DataFrame(columns=list(row(1)))


def test_incomplete_historical_scope_creates_ambiguity():
    old = df(row(1, doc=False))
    new = df(row(2, doc=True))
    s = summarize_ambiguous_recurrences(old, new)
    assert s['historicos_incompletos'] == 1
    assert s['ambiguidade_historico_incompleto'] == 1
    assert s['ambiguos_total'] == 1
    assert s['chave_parcial_igual_sem_documento'] == 1
    assert s['bloqueios_totais'] == 1


def test_incomplete_scope_can_have_different_partial_values_but_remains_blocked():
    old = df(row(1, doc=False, amount=10))
    new = df(row(2, doc=True, amount=20))
    s = summarize_ambiguous_recurrences(old, new)
    assert s['chave_parcial_diferente_sem_documento'] == 1
    assert s['ambiguos_total'] == 1
    assert s['bloqueios_totais'] == 1


def test_different_scope_does_not_create_ambiguity():
    old = df(row(1, doc=False))
    new = df(row(2, doc=True, period='2026-04'))
    s = summarize_ambiguous_recurrences(old, new)
    assert s['ambiguos_total'] == 0
    assert s['bloqueios_totais'] == 0


def test_same_exact_id_is_recognized_without_ambiguity():
    old = df(row(1, doc=False))
    new = df(row(1, doc=False))
    s = summarize_ambiguous_recurrences(old, new)
    assert s['ids_reconhecidos'] == 1
    assert s['ambiguos_total'] == 0
    assert s['bloqueios_totais'] == 0


def test_nonexclusive_full_key_is_ambiguous():
    old = df(row(1))
    new = df(row(2), row(3))
    s = summarize_ambiguous_recurrences(old, new)
    assert s['ambiguidade_chave_nao_exclusiva'] == 2
    assert s['ambiguos_total'] == 2
    assert s['bloqueios_totais'] == 2


def test_missing_document_in_new_scope_is_permitted_by_existing_rule():
    old = df(row(1, doc=True, period='2026-02'))
    new = df(row(2, doc=False, period='2026-03'))
    s = summarize_ambiguous_recurrences(old, new)
    assert s['candidatos_sem_chave_completa'] == 1
    assert s['candidatos_sem_chave_liberados_por_escopo'] == 1
    assert s['bloqueios_totais'] == 0
