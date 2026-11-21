from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.review.ke24_review_context import (
    ALERT_PATTERN, ALIAS_PATTERN, ALLOWED_CLASSIFICATION, CONTEXT_COLUMNS, CONTEXT_HEADERS,
    LEVELS, MEASURE_PATTERN, PERIOD_PATTERN, REASON_CODE_PATTERN,
    build_review_context,
)
from src.review.ke24_review_sheet import MAX_ALERTS, validate_review_sheet_matrix


@dataclass(frozen=True)
class ContextPlan:
    existing_count: int
    new_count: int
    new_rows: tuple


def validate_context_matrix(matrix):
    if (not isinstance(matrix, (tuple, list)) or not matrix
            or not isinstance(matrix[0], (tuple, list))
            or list(matrix[0]) != list(CONTEXT_HEADERS)
            or len(matrix) - 1 > MAX_ALERTS):
        raise ValueError("Cabecalho ou tamanho da aba de contexto invalido.")
    seen = {}
    for row in matrix[1:]:
        if (not isinstance(row, (tuple, list)) or len(row) != len(CONTEXT_COLUMNS)
                or any(not isinstance(v, str) or not v
                       or len(v) > 250 or any(c in v for c in "\n\r\t")
                       or v.startswith(("=", "+", "-", "@")) for v in row)):
            raise ValueError("Linha invalida ou formula na aba de contexto.")
        (aid, alias, period, signal, reason, explanation, measure,
         level, classification, classification_text, guidance) = row
        if (not ALERT_PATTERN.fullmatch(aid) or aid in seen
                or not ALIAS_PATTERN.fullmatch(alias)
                or not PERIOD_PATTERN.fullmatch(period)
                or not REASON_CODE_PATTERN.fullmatch(reason)
                or not MEASURE_PATTERN.fullmatch(measure)
                or level not in LEVELS or classification not in ALLOWED_CLASSIFICATION):
            raise ValueError("Identidade ou contrato semantico de contexto invalido.")
        seen[aid] = list(row)
    return seen


def plan_context_sync(history: pd.DataFrame, aliases: dict,
                      queue_matrix, context_matrix=None):
    queue_ids = set(validate_review_sheet_matrix(queue_matrix))
    if not queue_ids:
        raise RuntimeError("Fila operacional vazia; nao gerar contexto.")
    context = build_review_context(history, aliases)
    if set(context["alert_id"]) != queue_ids:
        raise RuntimeError(
            "Fila e historico divergem; sincronize a fila antes de criar contexto."
        )
    context = context.sort_values(["period_key", "alert_id"], kind="stable")
    desired = [list(CONTEXT_HEADERS), *context.astype(str).values.tolist()]
    planned = validate_context_matrix(desired)
    if context_matrix is None:
        existing = {}
    else:
        existing = validate_context_matrix(context_matrix)
        if not set(existing).issubset(planned):
            raise RuntimeError("Contexto remoto contem ID nao presente na fila.")
        for aid, old_row in existing.items():
            if old_row != planned[aid]:
                raise RuntimeError(
                    "Contexto existente diverge da origem; nao sobrescrever."
                )
    new_rows = tuple(tuple(row) for row in desired[1:] if row[0] not in existing)
    return ContextPlan(len(existing), len(new_rows), new_rows)
