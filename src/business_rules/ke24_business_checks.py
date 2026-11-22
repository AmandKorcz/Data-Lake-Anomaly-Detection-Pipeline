from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pandas as pd

TOKEN_PATTERN = re.compile(r"TKN_[0-9a-f]{32}\Z")
PERIOD_PATTERN = re.compile(r"\d{4}-(0[1-9]|1[0-2])\Z")
CURRENCY_PATTERN = re.compile(r"[A-Z]{3}\Z")
REQUIRED = ("company_code", "currency_type", "currency", "period_key")
OUTPUT = (
    "rule_id", "check_type", "company_code", "currency", "period_key",
    "status", "reason", "deviation_ratio",
)


def _finite_number(value, *, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Limite numerico invalido na politica.")
    num = float(value)
    if not math.isfinite(num) or (nonnegative and num < 0):
        raise ValueError("Limite numerico invalido na politica.")
    return num


def validate_policy(policy):
    if not isinstance(policy, dict):
        raise ValueError("Politica precisa ser um objeto JSON.")
    if policy.get("schema_version") != 1 or policy.get("status") != "ACTIVE":
        raise ValueError("Politica v1 ativa obrigatoria.")
    if not isinstance(policy.get("policy_version"), str) or not policy["policy_version"].strip():
        raise ValueError("Versao da politica ausente.")
    if policy.get("currency_type") != 10:
        raise ValueError("Este contrato exige moeda de empresa (tipo 10).")
    history = policy.get("history")
    if not isinstance(history, dict) or type(history.get("previous_months")) is not int:
        raise ValueError("Configuracao de historico invalida.")
    if not 2 <= history["previous_months"] <= 12:
        raise ValueError("O historico deve conter de 2 a 12 meses anteriores.")
    metrics = policy.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError("Metricas de negocio ausentes.")
    for name, spec in metrics.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError("Identificador de metrica invalido.")
        if not isinstance(spec, dict) or type(spec.get("enabled")) is not bool:
            raise ValueError("Configuracao de metrica invalida.")
        if not spec["enabled"]:
            continue
        terms = spec.get("terms", [])
        prefixes = spec.get("prefixes", [])
        if not isinstance(terms, list) or not isinstance(prefixes, list):
            raise ValueError("Fonte de metrica invalida.")
        if not terms and not prefixes:
            raise ValueError("Metrica ativa sem formula.")
        for item in terms:
            if not isinstance(item, dict) or not re.fullmatch(r"[a-z][a-z0-9_]*", str(item.get("field", ""))):
                raise ValueError("Termo da formula invalido.")
            _finite_number(item.get("weight"))
        for prefix in prefixes:
            if not isinstance(prefix, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", prefix):
                raise ValueError("Prefixo de metrica invalido.")
        medium = _finite_number(spec.get("medium_ratio"), nonnegative=True)
        high = _finite_number(spec.get("high_ratio"), nonnegative=True)
        if not 0 < medium < high:
            raise ValueError("Limites relativos precisam respeitar 0 < medio < alto.")
    return policy


def load_policy(path=None):
    if path is None:
        path = Path(__file__).resolve().parents[2] / "config" / "ke24_business_checks.v1.json"
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_policy(document)


def _validate_input(df):
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise ValueError("Dataset processed ausente ou vazio.")
    if df.columns.duplicated().any() or any(col not in df.columns for col in REQUIRED):
        raise ValueError("Estrutura da KE24 invalida.")
    for value in df["company_code"]:
        if not isinstance(value, str) or not TOKEN_PATTERN.fullmatch(value):
            raise ValueError("Company code precisa estar protegido.")
    for value in df["currency"]:
        if not isinstance(value, str) or not CURRENCY_PATTERN.fullmatch(value):
            raise ValueError("Moeda invalida.")
    for value in df["period_key"]:
        if not isinstance(value, str) or not PERIOD_PATTERN.fullmatch(value):
            raise ValueError("Periodo invalido.")
    if df["currency_type"].isna().any():
        raise ValueError("Tipo de moeda ausente.")


def _calculate_metric(df, spec):
    result = pd.Series(0.0, index=df.index, dtype=float)
    resolved = []
    for term in spec.get("terms", []):
        name = term["field"]
        if name not in df.columns:
            raise ValueError("Campo financeiro ausente da KE24.")
        resolved.append((name, float(term["weight"])))
    for prefix in spec.get("prefixes", []):
        matches = [c for c in df.columns if c.startswith(prefix)]
        if not matches:
            raise ValueError("Grupo de campos financeiros nao encontrado.")
        resolved.extend((col, 1.0) for col in matches)
    names = [name for name, _ in resolved]
    if len(names) != len(set(names)):
        raise ValueError("Formula repete uma medida financeira.")
    for name, weight in resolved:
        values = pd.to_numeric(df[name], errors="coerce")
        if not values.notna().all() or not values.map(math.isfinite).all():
            raise ValueError("Medida financeira nula ou nao numerica.")
        result += values.astype(float) * weight
    if not result.map(math.isfinite).all():
        raise ValueError("Agregacao financeira nao finita.")
    return result


def _row(rule_id, kind, company, currency, period, status, reason, deviation=None):
    return {
        "rule_id": rule_id, "check_type": kind,
        "company_code": company, "currency": currency, "period_key": period,
        "status": status, "reason": reason, "deviation_ratio": deviation,
    }


def _currency_checks(df, company_currencies):
    observations = df[list(REQUIRED)].drop_duplicates()
    result = []
    for company, ctype, currency, period in observations.itertuples(index=False, name=None):
        # CURTP 10 = moeda da empresa, nao necessariamente moeda do documento.
        if str(ctype) != "10":
            status, reason = "VIOLATION", "unexpected_currency_type"
        elif company not in company_currencies:
            status, reason = "PENDING", "company_currency_not_configured"
        elif currency not in company_currencies[company]:
            status, reason = "VIOLATION", "unexpected_company_currency"
        else:
            status, reason = "PASS", "company_currency_ok"
        result.append(_row("KE24-BC-001", "company_currency", company, currency,
                           period, status, reason))
    return result


def _historical_checks(df, policy):
    data = df.loc[df["currency_type"].astype(str) == "10"].copy()
    if data.empty:
        return []
    history_months = policy["history"]["previous_months"]
    result = []
    for metric_name, spec in policy["metrics"].items():
        if not spec["enabled"]:
            continue
        # Agregar ANTES de comparar: volume mensal, nao valor de cada documento.
        data["_business_metric"] = _calculate_metric(data, spec)
        monthly = (
            data.groupby(["company_code", "currency", "period_key"], as_index=False,
                         sort=True)["_business_metric"].sum()
        )
        if not monthly["_business_metric"].map(math.isfinite).all():
            raise ValueError("Agregacao mensal invalida.")
        rule_id = f"KE24-BC-{metric_name.upper()}"
        for (company, currency), group in monthly.groupby(["company_code", "currency"]):
            history = {
                pd.Period(period, freq="M"): float(value)
                for period, value in zip(group["period_key"], group["_business_metric"])
            }
            for current_period in sorted(history):
                period_str = str(current_period)
                prev = [history.get(current_period - lag)
                        for lag in range(1, history_months + 1)]
                if any(value is None for value in prev):
                    status, reason, deviation = "PENDING", "insufficient_consecutive_history", None
                else:
                    reference = float(pd.Series(prev).median())
                    current = history[current_period]
                    if reference == 0:
                        status, reason, deviation = "PENDING", "zero_historical_baseline", None
                    else:
                        deviation = abs(current - reference) / abs(reference)
                        if reference * current < 0:
                            status, reason = "REVIEW_HIGH", "sign_reversal"
                        elif deviation >= spec["high_ratio"]:
                            status, reason = "REVIEW_HIGH", "monthly_change_high"
                        elif deviation >= spec["medium_ratio"]:
                            status, reason = "REVIEW_MEDIUM", "monthly_change_medium"
                        else:
                            status, reason = "PASS", "within_historical_band"
                result.append(_row(rule_id, metric_name, company, currency,
                                   period_str, status, reason, deviation))
    return result


def evaluate_business_checks(processed, policy, *, company_currencies=None):
    validate_policy(policy)
    _validate_input(processed)
    mapping = {} if company_currencies is None else company_currencies
    if not isinstance(mapping, dict):
        raise ValueError("Regras de moeda por empresa invalidas.")
    for company, currencies in mapping.items():
        if not isinstance(company, str) or not TOKEN_PATTERN.fullmatch(company):
            raise ValueError("Identificador de empresa nao protegido na regra.")
        if not isinstance(currencies, (list, tuple, set)) or not currencies:
            raise ValueError("Conjunto de moedas invalido.")
        if any(not isinstance(c, str) or not CURRENCY_PATTERN.fullmatch(c) for c in currencies):
            raise ValueError("Codigo de moeda invalido na regra.")
    rows = _currency_checks(processed, mapping)
    rows.extend(_historical_checks(processed, policy))
    return pd.DataFrame(rows, columns=OUTPUT)


def summarize_business_checks(results):
    if not isinstance(results, pd.DataFrame) or "status" not in results.columns:
        raise ValueError("Resultados de negocio invalidos.")
    counts = results["status"].value_counts().to_dict()
    return {status: int(counts.get(status, 0)) for status in (
        "PASS", "REVIEW_MEDIUM", "REVIEW_HIGH", "VIOLATION", "PENDING")}
