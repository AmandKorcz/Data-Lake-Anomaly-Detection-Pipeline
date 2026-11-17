from copy import deepcopy

import pandas as pd
import pytest

from src.ml.ke24_rules_readiness import assess_protected_rules, main


COMPANY = "TKN_" + "a" * 32
OTHER = "TKN_" + "b" * 32


def valid_rules():
    return {
        "rules_name": "SYNTHETIC_ONLY",
        "status": "experimental_auto_classification",
        "currency_basis": {"currency_type": 10},
        "business_thresholds_by_company": {
            COMPANY: {
                "currency": "BRL",
                "materiality_medium_min": 100.0,
                "materiality_high_min": 300.0,
            }
        },
        "technical_thresholds": {
            "pretrained_margin_medium_min": 0.01,
            "pretrained_margin_high_min": 0.10,
            "stability_medium_min": 40,
            "stability_high_min": 100,
        },
    }


def test_complete_rules_remain_experimental_and_not_approved():
    summary = assess_protected_rules(valid_rules())
    assert summary["status"] == "EXPERIMENTAL_REVIEW"
    assert summary["technical_complete"] is True
    assert summary["business_approved"] is False
    assert summary["publication_authorized"] is False
    assert summary["scope_checked"] is False


def test_approved_status_string_does_not_grant_approval():
    rules = valid_rules()
    rules["status"] = "approved"
    summary = assess_protected_rules(rules)
    assert summary["status"] == "PENDING_BUSINESS_APPROVAL"
    assert summary["business_approved"] is False


def test_missing_pretrained_margins_block_completeness():
    rules = valid_rules()
    del rules["technical_thresholds"]["pretrained_margin_medium_min"]
    del rules["technical_thresholds"]["pretrained_margin_high_min"]
    summary = assess_protected_rules(rules)
    assert summary["status"] == "INCOMPLETE"
    assert summary["missing_technical_thresholds"] == 2


def test_missing_one_company_threshold_is_pending():
    rules = valid_rules()
    rules["business_thresholds_by_company"][COMPANY]["materiality_high_min"] = None
    summary = assess_protected_rules(rules)
    assert summary["status"] == "INCOMPLETE"
    assert summary["company_rules_incomplete"] == 1
    assert summary["missing_company_thresholds"] == 1


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, "100"])
def test_invalid_money_thresholds_rejected(bad):
    rules = valid_rules()
    rules["business_thresholds_by_company"][COMPANY]["materiality_medium_min"] = bad
    with pytest.raises(ValueError, match="Limite"):
        assess_protected_rules(rules)


def test_equal_or_reversed_materiality_thresholds_rejected():
    for medium, high in ((300, 300), (400, 300)):
        rules = valid_rules()
        rules["business_thresholds_by_company"][COMPANY]["materiality_medium_min"] = medium
        rules["business_thresholds_by_company"][COMPANY]["materiality_high_min"] = high
        with pytest.raises(ValueError, match="crescentes"):
            assess_protected_rules(rules)


def test_technical_thresholds_range_and_order():
    rules = valid_rules()
    rules["technical_thresholds"]["stability_high_min"] = 120
    with pytest.raises(ValueError, match="faixa"):
        assess_protected_rules(rules)
    rules = valid_rules()
    rules["technical_thresholds"]["pretrained_margin_high_min"] = 0.01
    with pytest.raises(ValueError, match="crescentes"):
        assess_protected_rules(rules)


def test_legacy_percentiles_are_optional_but_validated_if_provided():
    rules = valid_rules()
    rules["technical_thresholds"].update({
        "anomaly_percentile_medium_min": 90,
        "anomaly_percentile_high_min": 95,
    })
    assert assess_protected_rules(rules)["status"] == "EXPERIMENTAL_REVIEW"
    rules["technical_thresholds"]["anomaly_percentile_high_min"] = None
    with pytest.raises(ValueError, match="legados"):
        assess_protected_rules(rules)


def test_protected_company_keys_are_required():
    rules = valid_rules()
    rules["business_thresholds_by_company"] = {"MY_COMPANY": rules["business_thresholds_by_company"][COMPANY]}
    with pytest.raises(ValueError, match="nao protegido"):
        assess_protected_rules(rules)


def test_wrong_currency_basis_rejected():
    rules = valid_rules()
    rules["currency_basis"]["currency_type"] = 30
    with pytest.raises(ValueError, match="Base de moeda"):
        assess_protected_rules(rules)


def test_invalid_currency_rejected():
    rules = valid_rules()
    rules["business_thresholds_by_company"][COMPANY]["currency"] = "NOT_A_CURRENCY"
    with pytest.raises(ValueError, match="Moeda"):
        assess_protected_rules(rules)


def test_missing_company_in_candidates_is_reported_only_as_count():
    candidates = pd.DataFrame([
        {"company_code": COMPANY, "currency": "BRL", "currency_type": 10},
        {"company_code": OTHER, "currency": "USD", "currency_type": 10},
    ])
    summary = assess_protected_rules(valid_rules(), candidates)
    assert summary["status"] == "INCOMPLETE"
    assert summary["scope_checked"] is True
    assert summary["scope_unique_company_currencies"] == 2
    assert summary["scope_company_missing"] == 1
    assert OTHER not in str(summary)


def test_currency_mismatch_is_reported_without_exposing_company():
    candidates = pd.DataFrame([
        {"company_code": COMPANY, "currency": "USD", "currency_type": 10},
    ])
    summary = assess_protected_rules(valid_rules(), candidates)
    assert summary["scope_currency_mismatch"] == 1
    assert COMPANY not in str(summary)


def test_candidate_scope_deduplicates_company_currency_pairs():
    candidates = pd.DataFrame([
        {"company_code": COMPANY, "currency": "brl", "currency_type": 10},
        {"company_code": COMPANY, "currency": "BRL", "currency_type": 10},
    ])
    summary = assess_protected_rules(valid_rules(), candidates)
    assert summary["scope_unique_company_currencies"] == 1
    assert summary["status"] == "EXPERIMENTAL_REVIEW"


def test_empty_candidate_scope_is_not_mistaken_for_total_coverage():
    candidates = pd.DataFrame(columns=["company_code", "currency", "currency_type"])
    summary = assess_protected_rules(valid_rules(), candidates)
    assert summary["scope_checked"] is True
    assert summary["scope_unique_company_currencies"] == 0
    assert summary["business_approved"] is False


def test_candidate_scope_rejects_raw_company_code():
    candidates = pd.DataFrame([
        {"company_code": "REAL_NAME", "currency": "BRL", "currency_type": 10}
    ])
    with pytest.raises(ValueError, match="nao protegida"):
        assess_protected_rules(valid_rules(), candidates)


def test_candidate_scope_rejects_wrong_currency_type():
    candidates = pd.DataFrame([
        {"company_code": COMPANY, "currency": "BRL", "currency_type": 30}
    ])
    with pytest.raises(ValueError, match="base de moeda"):
        assess_protected_rules(valid_rules(), candidates)


def test_input_not_mutated():
    rules = valid_rules()
    original = deepcopy(rules)
    assess_protected_rules(rules)
    assert rules == original


def test_private_check_masks_loader_exception(monkeypatch, capsys):
    import src.ml.load_ke24_alert_rules as loader
    def fail():
        raise RuntimeError("PRIVATE_PATH_OR_VALUE_SHOULD_NOT_APPEAR")
    monkeypatch.setattr(loader, "load_protected_alert_rules", fail)
    assert main(["--check-private"]) == 1
    output = capsys.readouterr().out
    assert "PRIVATE_PATH_OR_VALUE_SHOULD_NOT_APPEAR" not in output
    assert "BLOCKED" in output


def test_private_check_only_prints_safe_aggregates(monkeypatch, capsys):
    import src.ml.load_ke24_alert_rules as loader
    monkeypatch.setattr(loader, "load_protected_alert_rules", valid_rules)
    assert main(["--check-private"]) == 0
    output = capsys.readouterr().out
    assert "EXPERIMENTAL_REVIEW" in output
    assert COMPANY not in output
    assert "300.0" not in output
    assert "publicacao" in output.lower()


def test_incomplete_private_rules_exit_nonzero(monkeypatch, capsys):
    import src.ml.load_ke24_alert_rules as loader
    rules = valid_rules()
    del rules["technical_thresholds"]["pretrained_margin_medium_min"]
    monkeypatch.setattr(loader, "load_protected_alert_rules", lambda: rules)
    assert main(["--check-private"]) == 2
    assert "INCOMPLETE" in capsys.readouterr().out
