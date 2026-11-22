import pandas as pd

from src.business_rules.ke24_business_checks import (
    evaluate_business_checks,
    load_policy,
    summarize_business_checks,
)
from src.business_rules.ke24_company_currency import load_company_currency_rules

SAFE_BUSINESS_KEYS = (
    "business_checks_total",
    "business_checks_pass",
    "business_checks_review_medium",
    "business_checks_review_high",
    "business_checks_violation",
    "business_checks_pending",
    "business_pending_history",
    "business_pending_zero_baseline",
    "business_pending_company_currency",
    "business_pending_revenue",
    "business_pending_material",
)

_PENDING_REASONS = frozenset({
    "insufficient_consecutive_history",
    "zero_historical_baseline",
    "company_currency_not_configured",
})


def summarize_readonly_business_checks(results):
    required = {"rule_id", "check_type", "status", "reason"}
    if not isinstance(results, pd.DataFrame) or not required.issubset(results.columns):
        raise ValueError("Resultado das regras de negocio invalido.")
    if results.columns.duplicated().any():
        raise ValueError("Colunas duplicadas nas regras de negocio.")
    statuses = summarize_business_checks(results)
    if len(results) != sum(statuses.values()):
        raise ValueError("Status inesperado nos checks de negocio.")
    pending = results.loc[results["status"] == "PENDING"]
    if pending["reason"].isna().any() or not pending["reason"].isin(_PENDING_REASONS).all():
        raise ValueError("Motivo pendente nao reconhecido.")

    summary = {
        "business_checks_total": int(len(results)),
        "business_checks_pass": statuses["PASS"],
        "business_checks_review_medium": statuses["REVIEW_MEDIUM"],
        "business_checks_review_high": statuses["REVIEW_HIGH"],
        "business_checks_violation": statuses["VIOLATION"],
        "business_checks_pending": statuses["PENDING"],
        "business_pending_history": int(pending["reason"].eq("insufficient_consecutive_history").sum()),
        "business_pending_zero_baseline": int(pending["reason"].eq("zero_historical_baseline").sum()),
        "business_pending_company_currency": int(pending["reason"].eq("company_currency_not_configured").sum()),
        "business_pending_revenue": int(pending["check_type"].eq("revenue").sum()),
        "business_pending_material": int(pending["check_type"].eq("material_cost_components").sum()),
    }
    if summary["business_checks_pending"] != sum(summary[key] for key in (
        "business_pending_history", "business_pending_zero_baseline",
        "business_pending_company_currency",
    )):
        raise ValueError("Resumo de pendencias inconsistente.")
    assert tuple(summary) == SAFE_BUSINESS_KEYS
    return summary


def run_readonly_business_checks(processed, secret_key):
    policy = load_policy()
    company_currencies = load_company_currency_rules(secret_key)
    results = evaluate_business_checks(
        processed, policy, company_currencies=company_currencies,
    )
    return summarize_readonly_business_checks(results)
