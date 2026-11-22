from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from src.business_rules.ke24_business_checks import (
    evaluate_business_checks,
    load_policy,
    summarize_business_checks,
    validate_policy,
)
from src.pipeline.run_ke24_business_checks import _demo, main

COMPANY = "TKN_" + "a" * 32
OTHER = "TKN_" + "b" * 32


@pytest.fixture
def policy():
    return {
        "schema_version": 1, "policy_version": "1.0", "status": "ACTIVE",
        "currency_type": 10, "history": {"previous_months": 3},
        "metrics": {
            "revenue": {"enabled": True, "terms": [{"field": "revenue", "weight": 1}],
                        "medium_ratio": 0.30, "high_ratio": 0.60},
            "material": {"enabled": True, "prefixes": ["c_4rm_"],
                         "medium_ratio": 0.30, "high_ratio": 0.60},
            "contribution_margin": {"enabled": False, "terms": [],
                                    "medium_ratio": 0.30, "high_ratio": 0.60},
        },
    }


def row(period, revenue=100, material=-20, company=COMPANY, currency="MXN", ctype=10):
    return {
        "company_code": company, "currency": currency, "currency_type": ctype,
        "period_key": period, "revenue": revenue, "c_4rm_steel": material,
    }


def four_months(final=100):
    return pd.DataFrame([
        row("2026-01"), row("2026-02"), row("2026-03"), row("2026-04", final),
    ])


def result_for(df, policy, metric):
    res = evaluate_business_checks(df, policy, company_currencies={COMPANY: ["MXN"]})
    return res[(res.check_type == metric) & (res.period_key == "2026-04")].iloc[0]


def test_policy_active(policy):
    assert validate_policy(policy)["status"] == "ACTIVE"


def test_policy_rejects_unapproved_status(policy):
    policy["status"] = "experimental_auto_classification"
    with pytest.raises(ValueError):
        validate_policy(policy)


@pytest.mark.parametrize("medium, high", [(0, .5), (.7, .6), (-.1, .5), (float('nan'), .5), (True, .5)])
def test_bad_thresholds_rejected(policy, medium, high):
    policy["metrics"]["revenue"].update(medium_ratio=medium, high_ratio=high)
    with pytest.raises(ValueError):
        validate_policy(policy)


def test_no_lookahead(policy):
    # Linha de MAIO nao altera a linha de ABRIL.
    april = result_for(four_months(190), policy, "revenue")
    may = pd.concat([four_months(190), pd.DataFrame([row("2026-05", 999999)])])
    april_again = result_for(may, policy, "revenue")
    assert april["status"] == april_again["status"] == "REVIEW_HIGH"
    assert april["deviation_ratio"] == april_again["deviation_ratio"]


def test_high_deviation(policy):
    assert result_for(four_months(170), policy, "revenue")["status"] == "REVIEW_HIGH"


def test_medium_deviation(policy):
    assert result_for(four_months(140), policy, "revenue")["status"] == "REVIEW_MEDIUM"


def test_normal_deviation(policy):
    assert result_for(four_months(105), policy, "revenue")["status"] == "PASS"


def test_decrease_is_also_checked(policy):
    assert result_for(four_months(25), policy, "revenue")["status"] == "REVIEW_HIGH"


def test_sign_reversal_high(policy):
    res = result_for(four_months(-5), policy, "revenue")
    assert res["status"] == "REVIEW_HIGH"
    assert res["reason"] == "sign_reversal"


def test_missing_month_needs_consecutive_history(policy):
    df = pd.DataFrame([row("2026-01"), row("2026-03"), row("2026-04"), row("2026-05")])
    assert result_for(df, policy, "revenue")["status"] == "PENDING"


def test_zero_baseline_pending(policy):
    df = pd.DataFrame([row("2026-01", 0), row("2026-02", 0), row("2026-03", 0), row("2026-04", 100)])
    assert result_for(df, policy, "revenue")["reason"] == "zero_historical_baseline"


def test_company_currency_violation(policy):
    df = pd.DataFrame([row("2026-01", currency="USD")])
    result = evaluate_business_checks(df, policy, company_currencies={COMPANY: ["MXN"]})
    assert result[result.check_type == "company_currency"].iloc[0]["status"] == "VIOLATION"


def test_unknown_company_currency_pending(policy):
    result = evaluate_business_checks(pd.DataFrame([row("2026-01")]), policy)
    assert result[result.check_type == "company_currency"].iloc[0]["status"] == "PENDING"


def test_correct_currency_passes(policy):
    result = evaluate_business_checks(pd.DataFrame([row("2026-01")]), policy,
                                      company_currencies={COMPANY: ["MXN"]})
    assert result[result.check_type == "company_currency"].iloc[0]["status"] == "PASS"


def test_wrong_currency_type_violation(policy):
    result = evaluate_business_checks(pd.DataFrame([row("2026-01", ctype="B0")]), policy,
                                      company_currencies={COMPANY: ["MXN"]})
    assert result.iloc[0]["status"] == "VIOLATION"
    assert not (result.check_type == "revenue").any()


def test_company_currency_segmentation(policy):
    df = pd.concat([four_months(180), pd.DataFrame([row("2026-04", 500, company=OTHER)])], ignore_index=True)
    result = evaluate_business_checks(df, policy, company_currencies={COMPANY:["MXN"], OTHER:["MXN"]})
    other = result[(result.company_code == OTHER) & (result.check_type == "revenue")].iloc[0]
    assert other["status"] == "PENDING"


def test_multiple_documents_sum_monthly(policy):
    df = four_months(0)
    df = pd.concat([df, pd.DataFrame([row("2026-04", 80), row("2026-04", 20)])], ignore_index=True)
    assert result_for(df, policy, "revenue")["status"] == "PASS"


def test_missing_measure_blocks(policy):
    df = four_months().drop(columns=["revenue"])
    with pytest.raises(ValueError, match="Campo financeiro ausente"):
        evaluate_business_checks(df, policy)


def test_unprotected_company_blocks(policy):
    df = pd.DataFrame([row("2026-01", company="REAL_CODE")])
    with pytest.raises(ValueError, match="protegido"):
        evaluate_business_checks(df, policy)


def test_zero_financial_data_no_print(policy, capsys):
    result = evaluate_business_checks(four_months(), policy)
    aggregate = summarize_business_checks(result)
    assert "PASS" in aggregate
    assert "company_code" not in str(aggregate)
    assert capsys.readouterr().out == ""


def test_does_not_mutate_input(policy):
    df = four_months()
    original = df.copy(deep=True)
    evaluate_business_checks(df, policy)
    pd.testing.assert_frame_equal(df, original)


def test_missing_metric_formula_blocks(policy):
    policy["metrics"]["contribution_margin"]["enabled"] = True
    with pytest.raises(ValueError, match="sem formula"):
        validate_policy(policy)


def test_demo_runs_offline(capsys):
    assert main(["--demo"]) == 0
    out = capsys.readouterr().out
    assert "VIOLATION:" in out
    assert "REVIEW_HIGH:" in out
    assert "TKN_" not in out


def test_check_config_runs_offline(capsys):
    assert main(["--check-config"]) == 0
    assert "ACTIVE" in capsys.readouterr().out


def test_default_config_loads():
    p = Path(__file__).resolve().parents[1] / "config" / "ke24_business_checks.v1.json"
    assert load_policy(p)["status"] == "ACTIVE"


def test_invalid_company_rule_token_blocks(policy):
    with pytest.raises(ValueError):
        evaluate_business_checks(four_months(), policy, company_currencies={"MX01": ["MXN"]})


def test_currency_empty_list_blocks(policy):
    with pytest.raises(ValueError):
        evaluate_business_checks(four_months(), policy, company_currencies={COMPANY: []})
