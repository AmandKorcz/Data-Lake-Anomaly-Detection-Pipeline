from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd

TOKEN_PATTERN = re.compile(r"TKN_[0-9a-f]{32}\Z")
ALERT_PATTERN = re.compile(r"ALT_[0-9a-f]{32}\Z")
PERIOD_PATTERN = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])\Z")
ALIAS_PATTERN = re.compile(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9 ._()/-]{1,79}\Z")
MEASURE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
ALLOWED_CLASSIFICATION = frozenset({
    "EXPERIMENTAL_CLASSIFICATION", "PENDING_BUSINESS_RULE",
    "PENDING_TECHNICAL_RULE", "PENDING_BUSINESS_VALIDATION",
    "READY_FOR_FINAL_CLASSIFICATION",
})
LEVELS = frozenset({"LOW", "MEDIUM", "HIGH", "PENDING"})
CLASSIFICATION_EXPLANATIONS = {
    "EXPERIMENTAL_CLASSIFICATION": "Classificacao experimental; revisar com responsavel financeiro",
    "PENDING_BUSINESS_RULE": "Regras financeiras ainda nao aprovadas",
    "PENDING_TECHNICAL_RULE": "Regra tecnica de prioridade ainda nao definida",
    "PENDING_BUSINESS_VALIDATION": "Aguardando validacao de negocio",
    "READY_FOR_FINAL_CLASSIFICATION": "Classificacao preparada, ainda nao e comprovacao de erro",
}
REQUIRED = (
    "alert_id", "company_code", "period_key", "alert_level",
    "classification_status", "signal_method", "reference_p95_flag",
    "anomaly_score", "training_p95_threshold", "score_margin",
    "dominant_measure", "alert_reason",
)
CONTEXT_SHEET_TITLE = "ke24_review_context"
REASON_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,119}\Z")
REASON_EXPLANATIONS = {
    "classification_rules_not_ready": "Prioridade pendente: regras de classificacao nao concluidas",
    "classification_rules_not_approved": "Prioridade pendente: regras de classificacao sem aprovacao",
    "high_anomaly_high_materiality_stable_signal": "Desvio elevado, alta materialidade e sinal estavel",
    "high_anomaly_with_relevant_supporting_signal": "Desvio elevado com outro sinal de suporte",
    "medium_anomaly_high_materiality": "Desvio moderado com alta materialidade",
    "low_combined_priority": "Baixa prioridade combinada pelos sinais definidos",
}
CONTEXT_COLUMNS = (
    "alert_id", "company_label", "period_key", "signal_description",
    "alert_reason_code", "reason_description", "dominant_measure", "alert_level",
    "classification_status", "classification_explanation", "review_guidance",
)
CONTEXT_HEADERS = (
    "ID do alerta", "Empresa", "Competencia (AAAA-MM)", "Sinal estatistico",
    "Motivo tecnico", "Explicacao do motivo", "Medida predominante",
    "Prioridade", "Classificacao tecnica", "Situacao das regras",
    "Orientacao para analise",
)


def _finite(value):
    if isinstance(value, (bool, str)):
        raise ValueError("Sinal numerico invalido no historico.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Sinal numerico invalido no historico.") from None
    if not math.isfinite(number):
        raise ValueError("Sinal numerico invalido no historico.")
    return number


def build_review_context(history: pd.DataFrame, company_aliases: dict[str, str]):
    if (not isinstance(history, pd.DataFrame) or history.columns.duplicated().any()
            or not set(REQUIRED).issubset(history.columns)):
        raise ValueError("Historico protegido sem contrato de contexto completo.")
    if not isinstance(company_aliases, dict):
        raise ValueError("Mapeamento de empresa privado invalido.")
    for token, alias in company_aliases.items():
        if (not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token)
                or not isinstance(alias, str) or not ALIAS_PATTERN.fullmatch(alias)
                or alias.startswith(("=", "+", "-", "@"))):
            raise ValueError("Alias empresarial invalido ou nao autorizado.")
    if len(set(company_aliases.values())) != len(company_aliases):
        raise ValueError("Alias empresarial duplicado: risco de atribuir alerta incorretamente.")
    records = []
    seen = set()
    for row in history[list(REQUIRED)].itertuples(index=False, name=None):
        (alert_id, company, period, level, classification, signal_method,
         flag, score, threshold, margin, measure, reason) = row
        if not isinstance(alert_id, str) or not ALERT_PATTERN.fullmatch(alert_id) or alert_id in seen:
            raise ValueError("Identidade do alerta invalida ou duplicada.")
        seen.add(alert_id)
        if not isinstance(company, str) or not TOKEN_PATTERN.fullmatch(company):
            raise ValueError("Empresa nao protegida no historico.")
        if company not in company_aliases:
            raise ValueError("Empresa sem alias privado validado; contexto bloqueado.")
        if not isinstance(period, str) or not PERIOD_PATTERN.fullmatch(period):
            raise ValueError("Competencia invalida no historico.")
        if level not in LEVELS or classification not in ALLOWED_CLASSIFICATION:
            raise ValueError("Classificacao de alerta invalida.")
        if (signal_method != "training_p95" or type(flag) not in (bool, np.bool_)
                or not bool(flag)):
            raise ValueError("Sinal estatistico inesperado.")
        s, t, m = _finite(score), _finite(threshold), _finite(margin)
        if s < t or m < 0 or not math.isclose(s - t, m, rel_tol=0, abs_tol=1e-10):
            raise ValueError("Score e referencia P95 inconsistentes.")
        if not isinstance(measure, str) or not MEASURE_PATTERN.fullmatch(measure):
            raise ValueError("Medida tecnica nao padronizada; nao exportar texto livre.")
        if not isinstance(reason, str) or not REASON_CODE_PATTERN.fullmatch(reason):
            raise ValueError("Motivo de classificacao nao padronizado.")
        records.append({
            "alert_id": alert_id,
            "company_label": company_aliases[company],
            "period_key": period,
            "signal_description": "Pontuacao acima do limite P95 do modelo (anomalia estatistica)",
            "alert_reason_code": reason,
            "reason_description": REASON_EXPLANATIONS.get(
                reason, "Codigo tecnico de classificacao; conferir regra do modelo"),
            "dominant_measure": measure,
            "alert_level": level,
            "classification_status": classification,
            "classification_explanation": CLASSIFICATION_EXPLANATIONS[classification],
            "review_guidance": "Investigar e validar no fechamento; erro financeiro nao confirmado",
        })
    return pd.DataFrame(records, columns=CONTEXT_COLUMNS)
