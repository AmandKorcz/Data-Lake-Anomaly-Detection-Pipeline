import argparse
from datetime import datetime, timezone

from googleapiclient.discovery import build

from src.alerts.build_ke24_pretrained_outputs import build_pretrained_dashboard
from src.ingestion.google_drive_client import authenticate, authenticate_write
from src.loading.ke24_drive_alert_history import (
    append_new_alerts,
    get_alerts_folder_id,
    load_alert_history_state,
    publish_alert_history,
)
from src.pipeline.run_ke24_drive_alert_outputs import (
    build_pending_alert_outputs_in_memory,
)
from src.pipeline.run_ke24_drive_alert_preview import build_alert_preview_in_memory
from src.pipeline.run_ke24_drive_features import (
    build_features_in_memory,
    load_verified_processed_dataframe,
)
from src.privacy.privacy_runtime import load_privacy_runtime


def load_operational_statuses(history, *, key):
    if history.empty:
        return {}

    from src.review.ke24_review_google_sheets import (
        get_spreadsheet_id, read_review_sheet_matrix,
        verify_private_review_sheet,
    )
    from src.review.ke24_review_trusted_snapshot import load_snapshot
    from src.review.ke24_review_operational import operational_status_map

    spreadsheet_id = get_spreadsheet_id()
    credential = authenticate_write()
    sheets = build("sheets", "v4", credentials=credential, cache_discovery=False)
    drive = build("drive", "v3", credentials=credential, cache_discovery=False)
    sheet_id = verify_private_review_sheet(sheets, drive, spreadsheet_id)
    snapshot = load_snapshot(key, spreadsheet_id, sheet_id)
    matrix = read_review_sheet_matrix(sheets, spreadsheet_id)
    return operational_status_map(history, matrix, trusted_snapshot=snapshot)


def prepare_alerts_respecting_reviews(current, previous_history, statuses, policy):
    from src.review.ke24_review_operational import suppress_reviewed_recurrences

    eligible, suppressed = suppress_reviewed_recurrences(current, statuses)
    history, new_count = append_new_alerts(previous_history, eligible, policy)
    return history, new_count, suppressed



def prepare_alerts_with_business_recurrence(current, previous_history, statuses, policy):
    # KE24_BUSINESS_RECURRENCE_V1: não altera IDs ou registros históricos.
    from src.review.ke24_review_recurrence import plan_business_recurrences
    plan = plan_business_recurrences(previous_history, current, statuses)
    history, new_count = append_new_alerts(previous_history, plan.eligible, policy)
    return history, new_count, plan

def main():
    parser = argparse.ArgumentParser(description="KE24: historico protegido de alertas no Drive")
    parser.add_argument(
        "--publish", action="store_true",
        help="Publica snapshot + manifesto no Drive (somente se houver novos alertas).",
    )
    args = parser.parse_args()
    get_alerts_folder_id()

    print("\nKE24 - Persistencia do historico de alertas no Google Drive\n")
    print("Modo:", "PUBLICACAO EXPLICITA" if args.publish else "PREVIA - SEM GRAVACAO")

    policy, schema, key = load_privacy_runtime()
    read_service = build("drive", "v3", credentials=authenticate(), cache_discovery=False)
    state = load_alert_history_state(read_service, policy)
    statuses = load_operational_statuses(state.history, key=key)

    processed = load_verified_processed_dataframe(read_service, policy, schema)
    features, feature_manifest, _ = build_features_in_memory(processed, policy)
    preview = build_alert_preview_in_memory(features, feature_manifest, policy)
    current = build_pending_alert_outputs_in_memory(
        preview["candidates"], policy, datetime.now(timezone.utc)
    )["history"]

    history, new_count, recurrence = prepare_alerts_with_business_recurrence(
        current, state.history, statuses, policy
    )
    dashboard = build_pretrained_dashboard(history, policy)

    print("\nResumo (nenhum identificador ou valor financeiro exibido):")
    print(f"Candidatos p95 (sinais estatisticos): {preview['candidate_count']}")
    print(f"Candidatos estaveis nas 5 seeds: {preview['stable_all_seeds']}")
    print(f"Alertas anteriores no Drive: {len(state.history)}")
    print(f"Recorrencias tratadas (ID ou identidade de negocio): {recurrence.suppressed_reviewed}")
    print(f"Reapresentacoes ainda pendentes reconhecidas: {recurrence.duplicates_pending}")
    print(f"Correspondencias entre exportacoes: {recurrence.matched_business}")
    print(f"Correspondencias ambiguas: {recurrence.ambiguous_candidates}")
    print(f"Sem identificacao de negocio suficiente: {recurrence.unmatchable_candidates}")
    print(f"Sem documento, mas sem conflito com historico/lote: {recurrence.safe_unmatchable_candidates}")
    print(f"Candidatos que exigem revisao antes de publicar: {recurrence.publication_blockers}")
    print(f"Alertas novos: {new_count}")
    print(f"Total de alertas no historico: {len(history)}")
    print(f"Registros preparados para dashboard: {len(dashboard)}")
    print("Status operacional: coluna H da fila; historico original preservado.")
    print("Classificacao financeira: PENDENTE de regras de negocio aprovadas.")

    if not args.publish:
        if recurrence.publication_blockers:
            print("ATENCAO: candidatos sem correspondencia segura; publicacao bloqueada ate revisao.")
        print("\nStatus: PREVIA APROVADA. Nenhum upload realizado.")
        return
    if recurrence.publication_blockers:
        raise RuntimeError(
            "Publicacao bloqueada: identidade de negocio ausente ou ambigua. "
            "Revise a previa antes de publicar novos alertas."
        )
    if new_count == 0:
        print("\nStatus: SEM ALTERACOES. Historico remoto preservado; nenhum upload.")
        return

    latest_statuses = load_operational_statuses(state.history, key=key)
    if latest_statuses != statuses:
        raise RuntimeError("Status alterado durante a execucao; refaca a previa.")

    write_service = build(
        "drive", "v3", credentials=authenticate_write(), cache_discovery=False
    )
    result = publish_alert_history(read_service, write_service, state, history, policy)
    print(f"\nStatus: PUBLICACAO VERIFICADA ({result['row_count']} alertas no historico).")
    print("Somente historico protegido foi publicado; nada no Google Sheets.")


if __name__ == "__main__":
    main()
