import pandas as pd
import pytest

from tools.ke24_new_company_preflight import (
    format_preflight, plan_new_company_preflight, validate_new_model_currency_type, main,
)
from src.loading.plan_ke24_incremental import MANIFEST_COLUMNS


def token(letter):
    return "TKN_" + letter * 32


def row(source, company, period, year=2026, count=30, currency="BRL", prefix=""):
    return dict(
        _source_file_sha256=token(source),
        company_code=token(company), period=period,
        period_year=f"{period}.{year}", currency=currency,
        record_count=count,
    )


def m(*items):
    return pd.DataFrame(items, columns=MANIFEST_COLUMNS)


def test_detects_new_company_and_model_ready_with_two_periods():
    old = m(row("a", "b", 1), row("b", "b", 2))
    new = m(row("c", "c", 1, count=20), row("d", "c", 2, count=1))
    aliases = {token("b"): "Empresa B", token("c"): "Empresa C"}
    result = plan_new_company_preflight(old, new, aliases)
    assert (result.existing_companies, result.new_companies, result.total_companies) == (1, 1, 2)
    assert (result.incoming_files, result.incoming_rows) == (2, 21)
    assert result.ready_for_e2e
    newcomer = next(g for g in result.groups if g.new_company)
    assert (newcomer.periods, newcomer.training_rows, newcomer.validation_rows) == (2,20,1)
    assert "Empresa C / BRL" in format_preflight(result, aliases)


def test_new_company_one_period_is_blocked():
    result = plan_new_company_preflight(m(), m(row("a", "b", 3, count=100)),
                                        {token("b"): "Empresa B"})
    assert result.model_blocking_groups == 1
    assert not result.ready_for_e2e
    assert result.groups[0].training_rows == 0


def test_train_too_small_even_when_total_rows_large():
    result = plan_new_company_preflight(
        m(), m(row("a", "b", 2, count=19), row("c", "b", 3, count=100)),
        {token("b"): "Empresa B"},
    )
    assert result.groups[0].training_rows == 19
    assert not result.ready_for_e2e


def test_existing_company_new_latest_period_moves_prior_holdout_into_train():
    old = m(row("a", "b", 1, count=21), row("c", "b", 2, count=7))
    new = m(row("d", "b", 3, count=2))
    result = plan_new_company_preflight(old, new, {token("b"): "Empresa B"})
    assert result.new_companies == 0
    assert (result.groups[0].training_rows, result.groups[0].validation_rows) == (28,2)
    assert result.ready_for_e2e


def test_missing_alias_is_blocker_without_identifier_leak():
    company = token("b")
    result = plan_new_company_preflight(m(), m(row("a", "b", 1), row("c", "b", 2)), {})
    summary = format_preflight(result, {})
    assert result.missing_alias_companies == 1
    assert not result.ready_for_e2e
    assert "Empresa sem alias #1" in summary
    assert company not in summary
    assert "TKN_" not in summary


def test_new_currency_requires_own_training_history():
    old = m(row("a", "b", 1), row("c", "b", 2))
    new = m(row("d", "b", 1, currency="USD"))
    report = plan_new_company_preflight(old, new, {token("b"): "Empresa B"})
    assert report.new_companies == 0
    assert report.model_blocking_groups == 1
    assert not report.ready_for_e2e


def test_equivalent_period_formats_cannot_bypass_duplicate_check():
    old = m(row("a", "b", 1, year=2026))
    new = m(dict(row("c", "b", 1, year=2026), period_year="001.2026"))
    with pytest.raises(ValueError, match="Competencia ja existente"):
        plan_new_company_preflight(old, new, {token("b"): "Empresa B"})


def test_conflicting_company_periods_inside_new_batch_are_blocked():
    new = m(row("a", "b", 1), row("c", "b", 1))
    with pytest.raises(ValueError, match="repetidas"):
        plan_new_company_preflight(m(), new, {})


def test_exact_source_duplicate_is_blocked():
    old = m(row("a", "b", 1))
    new = m(row("a", "c", 2))
    with pytest.raises(ValueError, match="ja consta"):
        plan_new_company_preflight(old, new, {})


def test_no_new_files_is_readonly_baseline():
    old = m(row("a", "b", 1), row("c", "b", 2))
    report = plan_new_company_preflight(old, m(), {token("b"): "Empresa B"})
    assert report.incoming_files == 0 and report.new_companies == 0
    assert report.ready_for_e2e
    assert "Nenhuma extracao nova" in format_preflight(report, {token("b"): "Empresa B"})


def test_rejects_unprotected_company():
    with pytest.raises(ValueError, match="token"):
        plan_new_company_preflight(m(), m(dict(row("a", "b", 1), company_code="1234")), {})


def test_invalid_period_and_negative_count_are_blocked():
    with pytest.raises(ValueError):
        plan_new_company_preflight(m(), m(dict(row("a", "b", 1), period_year="2.2026")), {})
    with pytest.raises(ValueError):
        plan_new_company_preflight(m(), m(row("a", "b", 1, count=-10)), {})


def test_main_does_not_echo_sensitive_parser_errors(monkeypatch, capsys):
    import tools.ke24_new_company_preflight as module
    def fail():
        raise ValueError("original-company-code=REAL123; finance=98765")
    monkeypatch.setattr(module, "_read_only_preview", fail)
    assert main([]) == 1
    stdout = capsys.readouterr().out
    assert "REAL123" not in stdout and "98765" not in stdout
    assert "REPROVADO (ValueError)" in stdout


def test_rejects_unsupported_currency_type_before_publish():
    validate_new_model_currency_type(pd.DataFrame({"currency_type": [10, "10", 10.0]}))
    with pytest.raises(ValueError, match="nao suportado"):
        validate_new_model_currency_type(pd.DataFrame({"currency_type": [10, 30]}))
    with pytest.raises(ValueError):
        validate_new_model_currency_type(pd.DataFrame({"currency_type": [None]}))
