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
    print("Regras financeiras demonstrativas nao sao aplicadas a dados reais.")

    policy, schema, _ = load_privacy_runtime()
    read_service = build("drive", "v3", credentials=authenticate(), cache_discovery=False)
    state = load_alert_history_state(read_service, policy)
    processed = load_verified_processed_dataframe(read_service, policy, schema)
    features, feature_manifest, _ = build_features_in_memory(processed, policy)
    preview = build_alert_preview_in_memory(features, feature_manifest, policy)
    current = build_pending_alert_outputs_in_memory(
        preview["candidates"], policy, datetime.now(timezone.utc)
    )["history"]

    history, new_count = append_new_alerts(state.history, current, policy)
    dashboard = build_pretrained_dashboard(history, policy)

    print("\nResumo (nenhum identificador ou valor financeiro exibido):")
    print(f"Candidatos p95: {preview['candidate_count']}")
    print(f"Candidatos estaveis nas 5 seeds: {preview['stable_all_seeds']}")
    print(f"Alertas anteriores no Drive: {len(state.history)}")
    print(f"Alertas novos: {new_count}")
    print(f"Total de alertas no historico: {len(history)}")
    print(f"Registros preparados para dashboard: {len(dashboard)}")
    print("Classificacao financeira: PENDENTE de regras de negocio aprovadas.")

    if not args.publish:
        print("\nStatus: PREVIA APROVADA. Nenhum upload realizado.")
        return
    if new_count == 0:
        print("\nStatus: SEM ALTERACOES. Historico remoto preservado; nenhum upload.")
        return

    write_service = build(
        "drive", "v3", credentials=authenticate_write(), cache_discovery=False
    )
    result = publish_alert_history(read_service, write_service, state, history, policy)
    print(f"\nStatus: PUBLICACAO VERIFICADA ({result['row_count']} alertas no historico).")
    print("Somente historico protegido foi publicado; nada no Google Sheets.")


if __name__ == "__main__":
    main()
