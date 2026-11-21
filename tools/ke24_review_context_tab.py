from __future__ import annotations

import argparse

from src.review.ke24_review_company_aliases import (
    alias_file_path, create_empty_alias_file, load_private_company_aliases,
)
from src.review.ke24_review_context_sync import plan_context_sync
from src.review.ke24_review_context import CONTEXT_HEADERS
from src.review.ke24_review_google_sheets import (
    append_private_context_rows, create_private_context_tab,
    get_context_sheet_id, get_spreadsheet_id, read_private_context_matrix,
    read_review_sheet_matrix, verify_private_review_sheet,
)
from src.review.ke24_review_sync import plan_review_sheet_sync
from src.review.ke24_review_trusted_snapshot import (
    exclusive_review_writer, load_snapshot,
)


class ContextPublishIndeterminate(RuntimeError):
    pass


def reconcile_context(sheets, drive, spreadsheet_id, history, aliases, *,
                      key, publish=False, snapshot_file=None):
    queue_id = verify_private_review_sheet(sheets, drive, spreadsheet_id)
    baseline = load_snapshot(key, spreadsheet_id, queue_id, path=snapshot_file)
    queue = read_review_sheet_matrix(sheets, spreadsheet_id)
    queue_plan = plan_review_sheet_sync(history, [], queue, trusted_snapshot=baseline)
    if queue_plan.new_alert_count:
        raise RuntimeError("Novos alertas na origem: sincronize a fila antes do contexto.")
    context_id = get_context_sheet_id(sheets, drive, spreadsheet_id)
    old_context = (read_private_context_matrix(sheets, spreadsheet_id)
                   if context_id is not None else None)
    plan = plan_context_sync(history, aliases, queue, old_context)
    summary = {
        "queue_alerts": len(queue) - 1,
        "context_existing": plan.existing_count,
        "context_new": plan.new_count,
        "requests_not_authenticated": queue_plan.pending_request_count,
        "published": False,
    }
    if not publish:
        return summary
    if read_review_sheet_matrix(sheets, spreadsheet_id) != queue:
        raise RuntimeError("Fila alterada durante a previa; nenhuma publicacao de contexto.")
    if context_id is not None and read_private_context_matrix(sheets, spreadsheet_id) != old_context:
        raise RuntimeError("Aba de contexto alterada durante a conferencia.")
    if context_id is None:
        # One-time creation of a separate tab. The operational queue is not edited.
        context_id = create_private_context_tab(sheets, drive, spreadsheet_id)
        old_context = [list(CONTEXT_HEADERS)]
    if not plan.new_rows:
        return summary
    try:
        append_private_context_rows(sheets, spreadsheet_id, context_id, plan.new_rows)
    except Exception:
        raise ContextPublishIndeterminate(
            "Publicacao do contexto indeterminada; NAO repetir sem executar --preview."
        ) from None
    try:
        after = read_private_context_matrix(sheets, spreadsheet_id)
        if after != [*old_context, *[list(row) for row in plan.new_rows]]:
            raise RuntimeError("Conteudo inesperado apos append do contexto.")
        if read_review_sheet_matrix(sheets, spreadsheet_id) != queue:
            raise RuntimeError("Fila original alterada durante publicacao do contexto.")
        verify_private_review_sheet(sheets, drive, spreadsheet_id)
    except Exception:
        raise ContextPublishIndeterminate(
            "Contexto ou fila nao confirmados apos escrita; NAO repetir automaticamente."
        ) from None
    summary["published"] = True
    return summary


def _services():
    from googleapiclient.discovery import build
    from src.ingestion.google_drive_client import authenticate, authenticate_write
    from src.loading.ke24_drive_alert_history import load_alert_history_state
    from src.privacy.privacy_runtime import load_privacy_runtime

    policy, _, key = load_privacy_runtime()
    write_credential = authenticate_write()
    read_credential = authenticate()
    sheets = build("sheets", "v4", credentials=write_credential, cache_discovery=False)
    drive_write = build("drive", "v3", credentials=write_credential, cache_discovery=False)
    drive_read = build("drive", "v3", credentials=read_credential, cache_discovery=False)
    history = load_alert_history_state(drive_read, policy).history
    if history.empty:
        raise RuntimeError("Historico remoto de alertas vazio; contexto bloqueado.")
    return sheets, drive_write, history, key


def main(argv=None):
    parser = argparse.ArgumentParser(description="KE24: contexto de revisao PRIVADO")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--init-aliases", action="store_true", help="Cria JSON local vazio, sem rede")
    group.add_argument("--preview", action="store_true", help="Somente leitura, nenhum dado publicado")
    group.add_argument("--publish", action="store_true", help="Adiciona aba/contexto real")
    parser.add_argument("--confirm-publish", action="store_true", help="Confirmacao explicita")
    args = parser.parse_args(argv)
    if args.confirm_publish and not args.publish:
        parser.error("--confirm-publish somente com --publish")
    if args.publish and not args.confirm_publish:
        parser.error("Publicacao exige --confirm-publish")
    if args.init_aliases:
        path = create_empty_alias_file()
        print("Arquivo privado criado em:", path)
        print("Preencha com codigos de empresa autorizados antes de --preview.")
        return 0
    spreadsheet_id = get_spreadsheet_id()
    sheets, drive, history, key = _services()
    aliases = load_private_company_aliases(key)
    if args.publish:
        with exclusive_review_writer():
            summary = reconcile_context(
                sheets, drive, spreadsheet_id, history, aliases,
                key=key, publish=True,
            )
    else:
        summary = reconcile_context(
            sheets, drive, spreadsheet_id, history, aliases,
            key=key, publish=False,
        )
    print("KE24 - aba PRIVADA de contexto (sem revisao humana autenticada)")
    for field in ("queue_alerts", "context_existing", "context_new",
                  "requests_not_authenticated", "published"):
        print(f"{field}: {summary[field]}")
    print("Sinal estatistico NAO significa erro financeiro confirmado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
