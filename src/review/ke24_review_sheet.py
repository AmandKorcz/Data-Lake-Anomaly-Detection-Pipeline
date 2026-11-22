from __future__ import annotations

import math
import re

import pandas as pd

from src.review.ke24_review_workflow import (
    ALERT_ID_PATTERN,
    PENDING,
    REVIEW_STATUSES,
    _timestamp,
    build_review_projection,
)

SHEET_TITLE = "ke24_review_queue"
MAX_ALERTS = 5000
SHEET_COLUMNS = (
    "alert_id", "period_key", "alert_level", "classification_status",
    "review_status", "review_revision", "review_updated_at_utc",
    "review_requested_status",
)
IMMUTABLE_COLUMNS = SHEET_COLUMNS[:-1]
REQUEST_COLUMN = SHEET_COLUMNS[-1]
_PERIOD = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])\Z")
_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH", "PENDING"})
_CLASSIFICATIONS = frozenset({
    "EXPERIMENTAL_CLASSIFICATION", "PENDING_BUSINESS_RULE",
    "PENDING_TECHNICAL_RULE", "PENDING_BUSINESS_VALIDATION",
    "READY_FOR_FINAL_CLASSIFICATION",
})


def _valid_revision(value):
    return (
        type(value) is int and 0 <= value <= 1_000_000
    ) or (
        type(value) is float and math.isfinite(value)
        and value.is_integer() and 0 <= value <= 1_000_000
    )


def validate_review_sheet_matrix(matrix):
    if not isinstance(matrix, (tuple, list)) or not matrix:
        raise ValueError("Matriz da fila de revisao ausente ou invalida.")
    if not isinstance(matrix[0], (list, tuple)) or list(matrix[0]) != list(SHEET_COLUMNS):
        raise ValueError("Cabecalho da fila de revisao divergente.")
    if len(matrix) - 1 > MAX_ALERTS:
        raise ValueError("Volume da fila de revisao acima do limite.")
    by_id = {}
    for row in matrix[1:]:
        if not isinstance(row, (list, tuple)) or len(row) != len(SHEET_COLUMNS):
            raise ValueError("Linha invalida na fila de revisao.")
        alert, period, level, classification, review, revision, timestamp, requested = row
        if not isinstance(alert, str) or not ALERT_ID_PATTERN.fullmatch(alert):
            raise ValueError("ID nao protegido na fila de revisao.")
        if alert in by_id:
            raise ValueError("Alerta duplicado na fila de revisao.")
        if not isinstance(period, str) or not _PERIOD.fullmatch(period):
            raise ValueError("Periodo invalido na fila de revisao.")
        if (not isinstance(level, str) or not isinstance(classification, str)
                or level not in _LEVELS or classification not in _CLASSIFICATIONS):
            raise ValueError("Classificacao tecnica invalida na fila de revisao.")
        if (not isinstance(review, str) or not isinstance(requested, str)
                or review not in REVIEW_STATUSES or requested not in REVIEW_STATUSES):
            raise ValueError("Status de revisao invalido na fila.")
        if not _valid_revision(revision):
            raise ValueError("Versao da revisao invalida na fila.")
        revision = int(revision)
        if revision == 0:
            if review != PENDING or timestamp not in ("", None):
                raise ValueError("Revisao inicial inconsistente.")
            timestamp = ""
        else:
            if not isinstance(timestamp, str):
                raise ValueError("Data de revisao invalida.")
            _timestamp(timestamp)
        by_id[alert] = [alert, period, level, classification, review, revision, timestamp, requested]
    return by_id


def build_review_sheet_matrix(alerts: pd.DataFrame, events):
    if not isinstance(alerts, pd.DataFrame) or alerts.columns.duplicated().any():
        raise ValueError("Historico protegido invalido.")
    necessary = {"alert_id", "period_key", "alert_level", "classification_status"}
    if not necessary.issubset(alerts.columns):
        raise ValueError("Historico sem campos minimos para a fila.")
    if len(alerts) > MAX_ALERTS:
        raise ValueError("Fila de revisao acima do limite.")
    projected = build_review_projection(alerts, events)
    ordered = projected.sort_values(["period_key", "alert_id"], kind="stable")
    matrix = [list(SHEET_COLUMNS)]
    for record in ordered.to_dict("records"):
        timestamp = record["review_updated_at_utc"]
        if pd.isna(timestamp):
            timestamp = ""
        matrix.append([
            record["alert_id"], record["period_key"], record["alert_level"],
            record["classification_status"], record["review_status"],
            int(record["review_revision"]),
            timestamp, record["review_status"],
        ])
    validate_review_sheet_matrix(matrix)
    return matrix


def build_status_dropdown_request(sheet_id, *, max_alerts=MAX_ALERTS):
    if type(sheet_id) is not int or sheet_id < 0:
        raise ValueError("Identificador da aba invalido.")
    if type(max_alerts) is not int or not 1 <= max_alerts <= MAX_ALERTS:
        raise ValueError("Capacidade da fila invalida.")
    index = len(SHEET_COLUMNS) - 1
    return {"setDataValidation": {
        "range": {
            "sheetId": sheet_id, "startRowIndex": 1,
            "endRowIndex": max_alerts + 1,
            "startColumnIndex": index, "endColumnIndex": index + 1,
        },
        "rule": {
            "condition": {
                "type": "ONE_OF_LIST",
                "values": [{"userEnteredValue": s} for s in REVIEW_STATUSES],
            },
            "strict": True,
            "showCustomUi": True,
        },
    }}
