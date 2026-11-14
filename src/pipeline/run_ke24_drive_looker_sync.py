import argparse
import os
import socket

from googleapiclient.discovery import build

from src.ingestion.google_drive_client import authenticate, authenticate_write
from src.loading.ke24_drive_alert_history import load_alert_history_state
from src.loading.ke24_looker_sheet import (
    SHEET_ID_ENV,
    build_matrix,
    check_existing_sheet,
    create_empty_sheet,
    get_spreadsheet_id,
    inspect_sheet,
    publish_sheet,
    read_sheet_matrix,
)
from src.privacy.privacy_runtime import load_privacy_runtime


def use_ipv4_for_this_process():
    original = socket.getaddrinfo

    def ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
        return original(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = ipv4_only


def main():
    parser = argparse.ArgumentParser(description="KE24 - Google Sheets para Looker Studio")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--create-sheet", action="store_true", help="Cria SOMENTE planilha vazia.")
    group.add_argument("--publish", action="store_true", help="Publica a visao minima no Sheets.")
    parser.add_argument("--ipv4", action="store_true", help="Forca IPv4 apenas neste processo.")
    args = parser.parse_args()
    if args.ipv4:
        use_ipv4_for_this_process()

    print("\nKE24 - Integracao Google Sheets / Looker Studio\n")
    print("A exportacao nao inclui valores financeiros nem documentos de referencia.")
    print("Os alertas ainda nao representam erros financeiros confirmados.")

    if args.create_sheet:
        if os.getenv(SHEET_ID_ENV, "").strip():
            raise RuntimeError(
                "Ja existe ID de planilha configurado. Verifique o destino existente; "
                "nao crie outra por acidente."
            )
        print("Modo: CRIACAO EXPLICITA DE PLANILHA VAZIA (nenhum alerta sera enviado)")
        write_credential = authenticate_write()
        sheets_service = build("sheets", "v4", credentials=write_credential, cache_discovery=False)
        try:
            created = create_empty_sheet(sheets_service)
        except Exception as exc:
            raise RuntimeError(
                "Criacao de planilha indeterminada. Confira seu Drive antes de repetir."
            ) from exc
        print("Planilha criada. Abra-a e confira as permissoes antes de continuar.")
        print("ID (somente no seu terminal):", created["spreadsheetId"])
        print("Link:", created.get("spreadsheetUrl", "consulte o Drive"))
        print(f"Configure na sessao: $env:{SHEET_ID_ENV} = '<ID_DA_PLANILHA>'")
        return

    print("Modo:", "PUBLICACAO EXPLICITA" if args.publish else "PREVIA - SEM GRAVACAO")
    policy, _, _ = load_privacy_runtime()
    drive_read = build("drive", "v3", credentials=authenticate(), cache_discovery=False)
    state = load_alert_history_state(drive_read, policy)
    if not state.snapshots:
        raise RuntimeError("Historico protegido ainda nao foi publicado no Drive.")
    desired = build_matrix(state.history, policy)
    print("Historico verificado do Drive:", len(state.history), "alertas")
    print("Registros de exportacao para Looker:", len(desired) - 1)
    print("Colunas da visao minimizada:", len(desired[0]))
    print("Status financeiro: PENDENTE DE REGRAS APROVADAS")

    if not args.publish and not os.getenv(SHEET_ID_ENV, "").strip():
        print(f"Configure {SHEET_ID_ENV} apos usar --create-sheet.")
        print("Status: PREVIA DA FONTE APROVADA. Nenhum dado enviado ao Sheets.")
        return

    spreadsheet_id = get_spreadsheet_id()
    cred = authenticate_write()
    sheets_service = build("sheets", "v4", credentials=cred, cache_discovery=False)
    drive_write = build("drive", "v3", credentials=cred, cache_discovery=False)
    inspect_sheet(sheets_service, drive_write, spreadsheet_id)
    existing = read_sheet_matrix(sheets_service, spreadsheet_id)
    previous_count = check_existing_sheet(existing, desired)
    print("Alertas ja presentes no Sheets:", previous_count)
    print("Alertas a sincronizar:", len(desired) - 1 - previous_count)

    if not args.publish:
        print("Status: PREVIA APROVADA. Nenhum upload realizado.")
        return

    # Confirma que nao houve alteracao dos dados de origem antes de escrever.
    reloaded = load_alert_history_state(drive_read, policy)
    if (reloaded.manifest_id != state.manifest_id
            or reloaded.manifest_sha256 != state.manifest_sha256
            or reloaded.snapshots != state.snapshots):
        raise RuntimeError("Historico do Drive mudou; refaca a previa.")
    result = publish_sheet(sheets_service, drive_write, spreadsheet_id, desired, existing)
    if result["published"]:
        print(f"Status: SINCRONIZACAO VERIFICADA ({result['rows']} alertas na planilha).")
    else:
        print(f"Status: SEM ALTERACOES ({result['rows']} alertas). Nenhuma escrita.")


if __name__ == "__main__":
    main()
