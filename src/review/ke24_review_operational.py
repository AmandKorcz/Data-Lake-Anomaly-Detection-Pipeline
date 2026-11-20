from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.review.ke24_review_sheet import validate_review_sheet_matrix
from src.review.ke24_review_sync import plan_review_sheet_sync


@dataclass(frozen=True)
class OperationalReviewSummary:
    total_history: int
    reviewed: int
    pending: int
    by_status: dict[str, int]


def operational_status_map(history: pd.DataFrame, observed_matrix, *, trusted_snapshot):
    if not isinstance(history, pd.DataFrame) or "alert_id" not in history.columns:
        raise ValueError("Historico sem identificadores de alerta.")
    if history.columns.duplicated().any() or history["alert_id"].duplicated().any():
        raise ValueError("Historico de alertas duplicado ou invalido.")
    if trusted_snapshot is None:
        raise ValueError("Snapshot local assinado obrigatorio.")
    plan = plan_review_sheet_sync(
        history, [], observed_matrix, trusted_snapshot=trusted_snapshot
    )
    if plan.new_alert_count:
        raise RuntimeError("Fila defasada: sincronize os alertas antes desta operacao.")
    observed = validate_review_sheet_matrix(observed_matrix)
    ids = set(history["alert_id"])
    if set(observed) != ids:
        raise RuntimeError("Historico e fila divergem; nao inferir status.")
    return {aid: row[-1] for aid, row in observed.items()}


def suppress_reviewed_recurrences(current: pd.DataFrame, statuses: dict[str, str]):
    if not isinstance(current, pd.DataFrame) or "alert_id" not in current.columns:
        raise ValueError("Candidatos de alerta invalidos.")
    if current.columns.duplicated().any() or current["alert_id"].duplicated().any():
        raise ValueError("Identificadores de alerta duplicados.")
    if not isinstance(statuses, dict) or any(
        not isinstance(key, str) or status not in {
            "PENDING", "IN_REVIEW", "CONFIRMED_ERROR", "CORRECTED", "NOT_AN_ERROR"
        } for key, status in statuses.items()
    ):
        raise ValueError("Mapa de status operacional invalido.")
    from src.review.ke24_review_workflow import ALERT_ID_PATTERN
    if not all(isinstance(aid, str) and ALERT_ID_PATTERN.fullmatch(aid)
               for aid in current["alert_id"].tolist()):
        raise ValueError("Identificadores nao protegidos.")
    handled = {aid for aid, state in statuses.items() if state != "PENDING"}
    mask = current["alert_id"].isin(handled)
    return current.loc[~mask].copy(), int(mask.sum())


def summarize_statuses(statuses: dict[str, str]):
    counts = {status: sum(value == status for value in statuses.values()) for status in (
        "PENDING", "IN_REVIEW", "CONFIRMED_ERROR", "CORRECTED", "NOT_AN_ERROR"
    )}
    return OperationalReviewSummary(
        total_history=len(statuses),
        reviewed=len(statuses) - counts["PENDING"],
        pending=counts["PENDING"],
        by_status=counts,
    )
