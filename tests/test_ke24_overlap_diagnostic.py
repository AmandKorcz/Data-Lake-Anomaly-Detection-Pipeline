import pandas as pd
import pytest

from tools.diagnose_ke24_overlap import summarize_overlaps

A = "TKN_" + "a" * 32
B = "TKN_" + "b" * 32
C = "TKN_" + "c" * 32


def test_independent_overlap_but_no_shared_pair():
    old = pd.DataFrame([
        {"company_code": A, "period_key": "2026-03"},
        {"company_code": B, "period_key": "2026-04"},
    ])
    new = pd.DataFrame([
        {"company_code": A, "period_key": "2026-04"},
        {"company_code": B, "period_key": "2026-03"},
    ])
    stats = summarize_overlaps(old, new)
    assert [s["chaves_compartilhadas"] for s in stats] == [2, 2, 0]
    assert stats[-1]["candidatos_com_chave_compartilhada"] == 0


def test_no_companies_in_common_even_with_same_period():
    old = pd.DataFrame([{"company_code": A, "period_key": "2026-03"}])
    new = pd.DataFrame([{"company_code": C, "period_key": "2026-03"}])
    stats = summarize_overlaps(old, new)
    assert [s["chaves_compartilhadas"] for s in stats] == [0, 1, 0]


def test_shared_pair_counts_rows_instead_of_only_distinct_keys():
    old = pd.DataFrame([
        {"company_code": A, "period_key": "2026-03"},
        {"company_code": A, "period_key": "2026-03"},
    ])
    new = pd.DataFrame([
        {"company_code": A, "period_key": "2026-03"},
        {"company_code": A, "period_key": "2026-03"},
        {"company_code": A, "period_key": "2026-03"},
    ])
    stats = summarize_overlaps(old, new)
    assert stats[-1]["chaves_compartilhadas"] == 1
    assert stats[-1]["historicos_com_chave_compartilhada"] == 2
    assert stats[-1]["candidatos_com_chave_compartilhada"] == 3


def test_missing_values_skipped():
    old = pd.DataFrame([
        {"company_code": None, "period_key": "2026-03"},
        {"company_code": A, "period_key": "2026-04"},
    ])
    new = pd.DataFrame([{"company_code": A, "period_key": "2026-04"}])
    stats = summarize_overlaps(old, new)
    assert [s["chaves_compartilhadas"] for s in stats] == [1, 1, 1]
    assert stats[0]["historico_chaves_distintas"] == 1


def test_reject_missing_columns():
    with pytest.raises(ValueError, match="Faltam colunas"):
        summarize_overlaps(pd.DataFrame({"period_key": ["2026-03"]}), pd.DataFrame())
