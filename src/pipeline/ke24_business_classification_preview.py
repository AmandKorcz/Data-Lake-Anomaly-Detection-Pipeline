"""Resumo seguro da classificacao financeira opcional na previa do KE24.

Nunca armazena medidas, documentos, empresas ou regras privadas.
"""

import pandas as pd


LEVELS = frozenset({"LOW", "MEDIUM", "HIGH", "PENDING"})
STATUSES = frozenset({
    "EXPERIMENTAL_CLASSIFICATION",
    "PENDING_BUSINESS_RULE",
    "PENDING_TECHNICAL_RULE",
    "PENDING_BUSINESS_VALIDATION",
    "READY_FOR_FINAL_CLASSIFICATION",
})


def summarize_classified_candidates(classified, expected_count):
    """Devolve somente contagens inteiras, nunca dados financeiros.

    `HIGH` representa prioridade estimada, nao uma anomalia validada.
    """
    if type(expected_count) is not int or expected_count < 0:
        raise ValueError("Quantidade esperada de candidatos invalida.")
    if not isinstance(classified, pd.DataFrame):
        raise ValueError("Resultado da classificacao indisponivel.")
    if classified.columns.duplicated().any():
        raise ValueError("Colunas duplicadas na classificacao.")
    if not {"alert_level", "classification_status"}.issubset(classified.columns):
        raise ValueError("Campos de classificacao ausentes.")
    if len(classified) != expected_count:
        raise ValueError("A classificacao alterou a quantidade de candidatos.")

    levels = classified["alert_level"].astype("string")
    statuses = classified["classification_status"].astype("string")
    if (levels.isna().any() or statuses.isna().any()
            or not levels.isin(LEVELS).all()
            or not statuses.isin(STATUSES).all()):
        raise ValueError("Nivel ou status de classificacao invalido.")

    return {
        "classification_total": expected_count,
        "classification_pending": int(statuses.str.startswith("PENDING").sum()),
        "classification_experimental": int(statuses.eq("EXPERIMENTAL_CLASSIFICATION").sum()),
        "classification_ready": int(statuses.eq("READY_FOR_FINAL_CLASSIFICATION").sum()),
        "priority_high": int(levels.eq("HIGH").sum()),
        "priority_medium": int(levels.eq("MEDIUM").sum()),
        "priority_low": int(levels.eq("LOW").sum()),
    }
