from __future__ import annotations

import argparse
from dataclasses import dataclass

import pandas as pd

from src.review.ke24_review_sheet import (
    SHEET_COLUMNS, build_review_sheet_matrix,
    validate_review_sheet_matrix,
)


@dataclass(frozen=True)
class ReviewSheetPlan:
    desired_matrix: list
    new_alert_count: int
    pending_request_count: int
    pending_requests: tuple
    publication_authorized: bool = False


def plan_review_sheet_sync(alerts, events, observed_matrix=None, *, trusted_snapshot=None):
    canonical = build_review_sheet_matrix(alerts, events)
    current_by_id = validate_review_sheet_matrix(canonical)
    if observed_matrix is None or observed_matrix == []:
        if trusted_snapshot not in (None, []):
            raise ValueError("Fila existente desapareceu; reconciliacao manual necessaria.")
        return ReviewSheetPlan(canonical, len(current_by_id), 0, ())
    if trusted_snapshot is None:
        raise ValueError("Snapshot confiavel da fila ausente; publicacao bloqueada.")
    baseline = validate_review_sheet_matrix(trusted_snapshot)
    observed = validate_review_sheet_matrix(observed_matrix)
    if set(observed) != set(baseline):
        raise ValueError("Alertas removidos ou inseridos manualmente na fila.")
    if not set(baseline).issubset(current_by_id):
        raise ValueError("Alerta historico ausente na origem protegida.")

    generated = [list(SHEET_COLUMNS)]
    proposals = []
    for row in canonical[1:]:
        alert_id = row[0]
        if alert_id not in baseline:
            generated.append(list(row))
            continue
        prior = baseline[alert_id]
        actual = observed[alert_id]
        if actual[:-1] != prior[:-1]:
            raise ValueError("Campo tecnico da planilha alterado; sobrescrita bloqueada.")
        if row[1:4] != prior[1:4]:
            raise ValueError("Origem historica alterada; reconciliacao manual necessaria.")
        is_official_update = row[4:7] != prior[4:7]
        is_new_request = actual[-1] != prior[-1]
        if is_official_update and (is_new_request or prior[-1] != prior[4]):
            raise ValueError("Revisao concorrente detectada; recarregar a fila.")
        requested = actual[-1]
        if is_official_update and not is_new_request:
            requested = row[4]
        if requested != row[4]:
            proposals.append((alert_id, requested, row[5]))
        generated.append([*row[:-1], requested])

    validate_review_sheet_matrix(generated)
    return ReviewSheetPlan(
        desired_matrix=generated,
        new_alert_count=len(current_by_id) - len(baseline),
        pending_request_count=len(proposals),
        pending_requests=tuple(proposals),
        publication_authorized=False,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="KE24 - simular fila de revisao (offline)")
    parser.add_argument("--demo", action="store_true", help="Demo sintetica, sem rede/escrita")
    args = parser.parse_args(argv)
    if not args.demo:
        parser.print_help()
        return 0
    a, b = ("ALT_" + letter * 32 for letter in "ab")
    original = pd.DataFrame({
        "alert_id": [a], "period_key": ["2026-10"],
        "alert_level": ["PENDING"],
        "classification_status": ["PENDING_BUSINESS_RULE"],
    })
    snapshot = build_review_sheet_matrix(original, [])
    observed = [list(r) for r in snapshot]
    observed[1][-1] = "IN_REVIEW"
    new_batch = pd.concat([
        original,
        pd.DataFrame({
            "alert_id": [b], "period_key": ["2026-11"],
            "alert_level": ["PENDING"],
            "classification_status": ["PENDING_BUSINESS_RULE"],
        }),
    ], ignore_index=True)
    plan = plan_review_sheet_sync(new_batch, [], observed, trusted_snapshot=snapshot)
    print("KE24 - Fila de revisao - demo OFFLINE")
    print("Novos alertas:", plan.new_alert_count)
    print("Solicitacoes humanas aguardando autenticacao:", plan.pending_request_count)
    print("Publicacao autorizada:", plan.publication_authorized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
