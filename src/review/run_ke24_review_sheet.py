from __future__ import annotations

import argparse
import os

from src.review.ke24_review_google_sheets import (
    SPREADSHEET_ENV, append_new_review_alerts,
    create_private_review_sheet, get_spreadsheet_id,
    initialize_private_review_sheet, read_review_sheet_matrix,
    verify_private_review_sheet,
)
from src.review.ke24_review_sheet import SHEET_COLUMNS, validate_review_sheet_matrix
from src.review.ke24_review_sync import plan_review_sheet_sync
from src.review.ke24_review_trusted_snapshot import (
    load_snapshot, save_snapshot, snapshot_path, exclusive_review_writer,
)


class QueueReconciliationRequired(RuntimeError):
    """Remote state may have changed: no blind retries or overwrites."""


def reconcile_queue(sheets_service, drive_service, spreadsheet_id, alerts, *,
                    key, publish=False, snapshot_file=None):
    sheet_id = verify_private_review_sheet(sheets_service, drive_service, spreadsheet_id)
    previous = load_snapshot(key, spreadsheet_id, sheet_id, path=snapshot_file)
    observed = read_review_sheet_matrix(sheets_service, spreadsheet_id)
    plan = plan_review_sheet_sync(alerts, [], observed, trusted_snapshot=previous)
    info = {
        "existing_alerts": len(observed) - 1,
        "new_alerts": plan.new_alert_count,
        "requests_not_authenticated": plan.pending_request_count,
        "published": False,
    }
    if not publish or not plan.new_alert_count:
        return info

    old_ids = set(validate_review_sheet_matrix(previous))
    new_rows = [row for row in plan.desired_matrix[1:] if row[0] not in old_ids]
    if len(new_rows) != plan.new_alert_count:
        raise QueueReconciliationRequired("Novos alertas inconsistentes; escrita bloqueada.")

    fresh = read_review_sheet_matrix(sheets_service, spreadsheet_id)
    if fresh != observed:
        raise QueueReconciliationRequired(
            "Planilha alterada durante a conferencia; nenhuma escrita realizada."
        )
    try:
        append_new_review_alerts(sheets_service, spreadsheet_id, sheet_id, new_rows)
    except Exception:
        raise QueueReconciliationRequired(
            "Inclusao indeterminada; confira a fila antes de tentar novamente."
        ) from None

    try:
        after = read_review_sheet_matrix(sheets_service, spreadsheet_id)
        planned = validate_review_sheet_matrix(plan.desired_matrix)
        now = validate_review_sheet_matrix(after)
        if now != planned:
            raise ValueError("Divergencia pos-publicacao")
        save_snapshot(key, spreadsheet_id, sheet_id, after, path=snapshot_file)
    except Exception:
        raise QueueReconciliationRequired(
            "Verificacao apos escrita indeterminada; nao repita automaticamente."
        ) from None
    info["published"] = True
    return info


def _services():
    from googleapiclient.discovery import build
    from src.ingestion.google_drive_client import authenticate, authenticate_write
    from src.privacy.privacy_runtime import load_privacy_runtime

    policy, _, key = load_privacy_runtime()
    credential = authenticate_write()
    sheets = build("sheets", "v4", credentials=credential, cache_discovery=False)
    drive_write = build("drive", "v3", credentials=credential, cache_discovery=False)
    return sheets, drive_write, policy, key, authenticate

def _create_flow():
    sheets, drive_write, _, key, _ = _services()
    created = create_private_review_sheet(sheets, drive_write)
    spreadsheet_id = created["spreadsheetId"]
    print("Arquivo novo criado no Drive. ID para seu ambiente local:", spreadsheet_id)
    print("Link privado:", created.get("spreadsheetUrl", "consulte o Drive"))
    print("Se a configuracao falhar, NAO repita --create; verifique este arquivo no Drive.")
    try:
        sheet_id = initialize_private_review_sheet(sheets, drive_write, created)
        save_snapshot(key, spreadsheet_id, sheet_id, [list(SHEET_COLUMNS)])
    except Exception:
        raise RuntimeError(
            "Arquivo criado, mas a configuracao e indeterminada; concilie manualmente."
        ) from None
    print("Planilha PRIVADA configurada; colunas A:G protegidas e lista em H.")
    print(f"Configure $env:{SPREADSHEET_ENV} com o ID no PowerShell local.")
    return 0

def main(argv=None):
    parser = argparse.ArgumentParser(description="KE24: fila de revisao privada no Google Sheets")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true", help="Cria planilha PRIVADA vazia (escrita explicita)")
    group.add_argument("--preview", action="store_true", help="Confere fila, sem escrita de alertas")
    group.add_argument("--publish", action="store_true", help="Adiciona novos alertas, sem sobrescrever linhas")
    parser.add_argument("--confirm-publish", action="store_true", help="Confirmacao explicita da inclusao")
    args = parser.parse_args(argv)
    if args.confirm_publish and not args.publish:
        parser.error("--confirm-publish apenas com --publish")
    if args.publish and not args.confirm_publish:
        parser.error("Para publicar, use tambem --confirm-publish")

    if args.create:
        if os.environ.get(SPREADSHEET_ENV) or snapshot_path().exists():
            raise RuntimeError("Ja existe configuracao/snapshot; criar outra fila bloqueado.")
        with exclusive_review_writer():
            return _create_flow()


    spreadsheet_id = get_spreadsheet_id()
    sheets, drive_write, policy, key, authenticate = _services()
    from googleapiclient.discovery import build
    from src.loading.ke24_drive_alert_history import load_alert_history_state

    reader = build("drive", "v3", credentials=authenticate(), cache_discovery=False)
    history = load_alert_history_state(reader, policy).history
    if history.empty:
        raise RuntimeError("Historico de alertas vazio: nao sincronizar fila.")
    if args.publish:
        with exclusive_review_writer():
            outcome = reconcile_queue(
                sheets, drive_write, spreadsheet_id, history,
                key=key, publish=True,
            )
    else:
        outcome = reconcile_queue(
            sheets, drive_write, spreadsheet_id, history,
            key=key, publish=False,
        )
    print("KE24 - fila privada de revisao (sem identidade de analista)")
    for k in ("existing_alerts", "new_alerts", "requests_not_authenticated", "published"):
        print(f"{k}: {outcome[k]}")
    print("Pedidos humanos sao PROPOSTAS, nunca revisoes automaticamente aprovadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
