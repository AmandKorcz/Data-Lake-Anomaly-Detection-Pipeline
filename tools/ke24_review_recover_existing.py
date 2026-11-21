from __future__ import annotations

import argparse
from pathlib import Path

from src.review.ke24_review_google_sheets import (
    _owner_email,
    _spreadsheet_metadata,
    _target_metadata,
    get_spreadsheet_id,
    read_review_sheet_matrix,
    verify_private_review_sheet,
)
from src.review.ke24_review_sheet import SHEET_COLUMNS, SHEET_TITLE
from src.review.ke24_review_trusted_snapshot import (
    exclusive_review_writer, load_snapshot, save_snapshot, snapshot_path,
)


class ExistingSheetNotReady(RuntimeError):
    ""


def _safe_error(exc):
    if type(exc) in (ValueError, RuntimeError, ExistingSheetNotReady):
        return str(exc)
    return f"Falha externa ({type(exc).__name__}); nao exibir detalhes privados."


def diagnose_existing(sheets, drive, spreadsheet_id, *, baseline_file=None):
    outcome = {}
    target = None
    book = None
    try:
        target = _target_metadata(drive, spreadsheet_id)
        for name in ("shared", "trashed"):
            outcome[f"drive_{name}"] = (
                str(target[name]) if name in target else "OMITTED"
            )
        outcome["drive_can_edit"] = target.get("capabilities", {}).get("canEdit") is True
        try:
            _owner_email(target)
            outcome["owner_identified"] = True
        except (RuntimeError, ValueError):
            outcome["owner_identified"] = False
    except Exception as exc:
        outcome["drive_read"] = _safe_error(exc)

    try:
        book = _spreadsheet_metadata(sheets, spreadsheet_id)
        tabs = book.get("sheets", [])
        outcome["sheet_tabs"] = len(tabs)
        if len(tabs) == 1:
            tab = tabs[0]
            props = tab.get("properties", {})
            grid = props.get("gridProperties", {})
            outcome["sheet_title_ok"] = props.get("title") == SHEET_TITLE
            outcome["sheet_column_count_ok"] = grid.get("columnCount") == len(SHEET_COLUMNS)
            outcome["sheet_frozen_header_ok"] = grid.get("frozenRowCount") == 1
            protected = tab.get("protectedRanges", [])
            outcome["protected_range_count"] = len(protected)
            if len(protected) == 1:
                p = protected[0]
                region = p.get("range", {})
                outcome["protection_columns_A_G"] = (
                    region.get("startColumnIndex", 0) == 0
                    and region.get("endColumnIndex") == 7
                    and region.get("startRowIndex", 0) == 0
                    and "endRowIndex" not in region
                )
                outcome["protection_warningOnly"] = (
                    str(p["warningOnly"]) if "warningOnly" in p else "OMITTED_DEFAULT_FALSE"
                )
                outcome["protection_editors_present"] = bool(p.get("editors", {}).get("users"))
    except Exception as exc:
        outcome["sheet_metadata_read"] = _safe_error(exc)

    try:
        verify_private_review_sheet(sheets, drive, spreadsheet_id)
        outcome["private_contract"] = "APPROVED"
    except Exception as exc:
        outcome["private_contract"] = "BLOCKED"
        outcome["contract_reason"] = _safe_error(exc)

    try:
        observed = read_review_sheet_matrix(sheets, spreadsheet_id)
        outcome["header_ok"] = observed[0] == list(SHEET_COLUMNS)
        outcome["existing_alert_rows"] = len(observed) - 1
    except Exception as exc:
        outcome["header_ok"] = False
        outcome["read_reason"] = _safe_error(exc)

    target = snapshot_path() if baseline_file is None else Path(baseline_file)
    outcome["signed_snapshot_exists"] = target.is_file()
    return outcome


def adopt_existing(sheets, drive, spreadsheet_id, key, *, baseline_file=None, lock_file=None):
    baseline = snapshot_path() if baseline_file is None else Path(baseline_file)
    if lock_file is None and baseline_file is not None:
        lock_file = baseline.parent / ".review_recovery.lock"
    with exclusive_review_writer(path=lock_file):
        tab_id = verify_private_review_sheet(sheets, drive, spreadsheet_id)
        observed = read_review_sheet_matrix(sheets, spreadsheet_id)
        empty_header = [list(SHEET_COLUMNS)]
        if observed != empty_header:
            raise ExistingSheetNotReady(
                "Fila nao esta vazia: nao e seguro recriar um snapshot de origem."
            )
        if baseline.exists():
            loaded = load_snapshot(key, spreadsheet_id, tab_id, path=baseline)
            if loaded != empty_header:
                raise ExistingSheetNotReady("Snapshot anterior divergente; nao sobrescrever.")
            return "ALREADY_INITIALIZED"
        if verify_private_review_sheet(sheets, drive, spreadsheet_id) != tab_id:
            raise ExistingSheetNotReady("Aba alterada durante a verificacao.")
        if read_review_sheet_matrix(sheets, spreadsheet_id) != empty_header:
            raise ExistingSheetNotReady("Conteudo alterado durante a verificacao.")
        save_snapshot(key, spreadsheet_id, tab_id, empty_header, path=baseline)
        loaded = load_snapshot(key, spreadsheet_id, tab_id, path=baseline)
        if loaded != empty_header:
            raise ExistingSheetNotReady("Verificacao local apos gravacao falhou.")
        return "LOCAL_SNAPSHOT_RESTORED"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="KE24: diagnosticar/reconciliar planilha de revisao ja existente, sem escritas no Google"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--diagnose", action="store_true", help="Somente leitura e diagnostico sem dados")
    group.add_argument(
        "--adopt-existing", action="store_true",
        help="Somente se vazia e privada: criar snapshot HMAC LOCAL, sem escrita no Google",
    )
    args = parser.parse_args(argv)
    sid = get_spreadsheet_id()
    from src.review.run_ke24_review_sheet import _services
    sheets, drive, _, key, _ = _services()
    if args.diagnose:
        info = diagnose_existing(sheets, drive, sid)
        print("KE24 - diagnostico do arquivo JA EXISTENTE (somente leitura)")
        for k, v in info.items():
            print(f"{k}: {v}")
        return 0 if info.get("private_contract") == "APPROVED" and info.get("header_ok") else 2
    try:
        outcome = adopt_existing(sheets, drive, sid, key)
    except Exception as exc:
        print("RECUPERACAO BLOQUEADA:", _safe_error(exc))
        return 2
    print("KE24 - recuperacao local concluida:", outcome)
    print("Nenhuma linha de alerta publicada; nenhuma escrita no Google Sheets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
