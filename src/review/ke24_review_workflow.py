from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from uuid import uuid4

import pandas as pd

ALERT_ID_PATTERN = re.compile(r"ALT_[0-9a-f]{32}\Z")
REVIEWER_PATTERN = re.compile(r"REV_[0-9a-f]{32}\Z")
EVENT_ID_PATTERN = re.compile(r"REV_EVT_[0-9a-f]{32}\Z")

PENDING = "PENDING"
IN_REVIEW = "IN_REVIEW"
CONFIRMED_ERROR = "CONFIRMED_ERROR"
CORRECTED = "CORRECTED"
NOT_AN_ERROR = "NOT_AN_ERROR"

REVIEW_STATUSES = (
    PENDING, IN_REVIEW, CONFIRMED_ERROR, CORRECTED, NOT_AN_ERROR,
)

TRANSITIONS = {
    (PENDING, IN_REVIEW): "START_REVIEW",
    (IN_REVIEW, PENDING): "NEED_MORE_INFORMATION",
    (IN_REVIEW, CONFIRMED_ERROR): "FINANCIAL_ERROR_CONFIRMED",
    (IN_REVIEW, NOT_AN_ERROR): "EXPECTED_VARIATION",
    (CONFIRMED_ERROR, CORRECTED): "CORRECTION_VERIFIED",
    (CONFIRMED_ERROR, IN_REVIEW): "REOPENED",
    (CORRECTED, IN_REVIEW): "REOPENED",
    (NOT_AN_ERROR, IN_REVIEW): "REOPENED",
}

EVENT_FIELDS = frozenset({
    "event_id", "alert_id", "previous_status", "new_status", "revision",
    "reason", "reviewer_token", "reviewed_at_utc",
})

REVIEW_PROJECTION_FIELDS = (
    "review_status", "review_revision", "review_updated_at_utc",
)


class ReviewConflict(ValueError):
    """Revisão concorrente: atualizar a leitura antes de tentar novamente."""


def _check_identifier(value, pattern, label):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{label} invalido ou nao protegido.")


def _timestamp(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("Horario de revisao UTC invalido.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Horario de revisao UTC invalido.") from None
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("Horario de revisao UTC invalido.")
    return result


def _as_ids(alerts):
    if not isinstance(alerts, pd.DataFrame) or "alert_id" not in alerts.columns:
        raise ValueError("Lista de alertas invalida.")
    if alerts.columns.duplicated().any():
        raise ValueError("Colunas de alertas duplicadas.")
    if any(col in alerts.columns for col in REVIEW_PROJECTION_FIELDS):
        raise ValueError("Projecao de revisao ja existe; evitar sobrescrita.")
    ids = list(alerts["alert_id"])
    for value in ids:
        _check_identifier(value, ALERT_ID_PATTERN, "ID de alerta")
    if len(set(ids)) != len(ids):
        raise ValueError("IDs de alerta duplicados.")
    return ids


def replay_review_events(events, known_alert_ids):
    if not isinstance(events, (list, tuple)):
        raise ValueError("Historico de revisoes invalido.")
    try:
        known = set(known_alert_ids)
    except TypeError as exc:
        raise ValueError("Alertas conhecidos invalidos.") from None
    for alert_id in known:
        _check_identifier(alert_id, ALERT_ID_PATTERN, "ID de alerta")

    states = {}
    used_event_ids = set()
    last_time = {}
    for event in events:
        if not isinstance(event, dict) or set(event) != EVENT_FIELDS:
            raise ValueError("Contrato de evento de revisao invalido.")
        event_id = event["event_id"]
        _check_identifier(event_id, EVENT_ID_PATTERN, "ID de evento")
        if event_id in used_event_ids:
            raise ValueError("Evento de revisao duplicado.")
        used_event_ids.add(event_id)
        alert_id = event["alert_id"]
        _check_identifier(alert_id, ALERT_ID_PATTERN, "ID de alerta")
        if alert_id not in known:
            raise ValueError("Evento para alerta ausente ou nao autorizado.")
        _check_identifier(event["reviewer_token"], REVIEWER_PATTERN, "Revisor")
        when = _timestamp(event["reviewed_at_utc"])
        current = states.get(alert_id, (PENDING, 0, None))
        prior_status, prior_revision, _ = current
        next_status = event["new_status"]
        if (
            event["previous_status"] != prior_status
            or (prior_status, next_status) not in TRANSITIONS
            or event["reason"] != TRANSITIONS.get((prior_status, next_status))
        ):
            raise ValueError("Transicao de revisao invalida ou fora de ordem.")
        revision = event["revision"]
        if type(revision) is not int or revision != prior_revision + 1:
            raise ValueError("Revisao de evento inconsistente.")
        if alert_id in last_time and when < last_time[alert_id]:
            raise ValueError("Eventos de revisao fora de ordem temporal.")
        last_time[alert_id] = when
        states[alert_id] = (next_status, revision, event["reviewed_at_utc"])
    return states


def append_review_event(
    events, alerts, *, alert_id, new_status, expected_revision,
    reviewer_token, reviewed_at=None,
):
    ids = _as_ids(alerts)
    _check_identifier(alert_id, ALERT_ID_PATTERN, "ID de alerta")
    if alert_id not in set(ids):
        raise ValueError("Alerta ausente ou nao autorizado.")
    _check_identifier(reviewer_token, REVIEWER_PATTERN, "Revisor")
    states = replay_review_events(events, ids)
    current, revision, _ = states.get(alert_id, (PENDING, 0, None))
    if type(expected_revision) is not int or expected_revision != revision:
        raise ReviewConflict("Revisao mudou; atualize o estado antes de editar.")
    reason = TRANSITIONS.get((current, new_status))
    if reason is None:
        raise ValueError("Transicao nao permitida para este status.")
    if reviewed_at is None:
        reviewed_at = datetime.now(timezone.utc)
    if not isinstance(reviewed_at, datetime) or reviewed_at.tzinfo is None:
        raise ValueError("Data de revisao invalida.")
    offset = reviewed_at.utcoffset()
    if offset is None:
        raise ValueError("Data de revisao invalida.")
    timestamp = reviewed_at.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    entry = {
        "event_id": "REV_EVT_" + uuid4().hex,
        "alert_id": alert_id,
        "previous_status": current,
        "new_status": new_status,
        "revision": revision + 1,
        "reason": reason,
        "reviewer_token": reviewer_token,
        "reviewed_at_utc": timestamp,
    }
    updated = [*events, entry]
    replay_review_events(updated, ids)
    return updated


def build_review_projection(alerts, events):
    ids = _as_ids(alerts)
    states = replay_review_events(events, ids)
    result = alerts.copy(deep=True)
    result["review_status"] = [states.get(i, (PENDING, 0, None))[0] for i in ids]
    result["review_revision"] = [states.get(i, (PENDING, 0, None))[1] for i in ids]
    result["review_updated_at_utc"] = [states.get(i, (PENDING, 0, None))[2] for i in ids]
    return result


def summarize_review_status(projected):
    if not isinstance(projected, pd.DataFrame) or "review_status" not in projected.columns:
        raise ValueError("Projecao de revisao invalida.")
    statuses = projected["review_status"]
    if statuses.isna().any() or not statuses.isin(REVIEW_STATUSES).all():
        raise ValueError("Status de revisao invalido.")
    return {status: int(statuses.eq(status).sum()) for status in REVIEW_STATUSES}


def main(argv=None):
    parser = argparse.ArgumentParser(description="KE24 - regras offline de revisao humana")
    parser.add_argument("--demo", action="store_true", help="Exibe apenas contagens sinteticas")
    options = parser.parse_args(argv)
    if not options.demo:
        parser.print_help()
        return 0
    ids = ["ALT_" + c * 32 for c in "abc"]
    alerts = pd.DataFrame({"alert_id": ids, "investigation_status": ["DETECTED"] * 3})
    actor = "REV_" + "d" * 32
    events = append_review_event([], alerts, alert_id=ids[0], new_status=IN_REVIEW,
                                 expected_revision=0, reviewer_token=actor)
    print("KE24 - Demo sintetica (somente memoria; sem Drive/Sheets)")
    print("Identidade tecnica preservada:", alerts["investigation_status"].eq("DETECTED").all())
    for status, count in summarize_review_status(build_review_projection(alerts, events)).items():
        print(f"{status}: {count}")
    print("Publicacao autorizada: False")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
