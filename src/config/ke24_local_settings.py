import argparse
import os
import re
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_CONFIG = PROJECT_ROOT / "config" / ".env"
PROFITABILITY_SHEET_KEY = "KE24_PROFITABILITY_SPREADSHEET_ID"
FOLDER_KEYS = (
    "KE24_DRIVE_RAW_FOLDER_ID",
    "KE24_DRIVE_PROCESSED_FOLDER_ID",
    "KE24_DRIVE_ALERTS_FOLDER_ID",
)
OPTIONAL_KEYS = (
    "KE24_LOOKER_SPREADSHEET_ID",
    "KE24_REVIEW_SPREADSHEET_ID",
    PROFITABILITY_SHEET_KEY,
)
SUPPORTED_KEYS = (*FOLDER_KEYS, *OPTIONAL_KEYS)
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")


def _valid_id(name: str, value: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value.strip()):
        raise ValueError(f"Valor invalido para {name} (ID omitido).")
    return value.strip()


def _check_distinct_folders(values: dict[str, str]) -> None:
    present = [values[name] for name in FOLDER_KEYS if values.get(name)]
    if len(set(present)) != len(present):
        raise ValueError("Pastas raw, processed e alerts precisam ter IDs diferentes.")


def _check_distinct_sheets(values: dict[str, str]) -> None:
    present = [values[name] for name in OPTIONAL_KEYS if values.get(name)]
    if len(set(present)) != len(present):
        raise ValueError("As planilhas KE24 devem ter IDs diferentes.")


def load_ke24_local_settings(path: Path = LOCAL_CONFIG) -> tuple[str, ...]:
    path = Path(path)
    if not path.is_file():
        return ()

    entries = dotenv_values(path)
    updates: dict[str, str] = {}
    for name in SUPPORTED_KEYS:
        if name not in entries:
            continue
        raw_value = entries[name]
        if raw_value is None or not raw_value.strip():
            if name in FOLDER_KEYS:
                raise ValueError(f"{name} esta vazio em config/.env (ID omitido).")
            continue
        updates[name] = _valid_id(name, raw_value)

    effective_folders = {name: os.environ.get(name, "").strip() for name in FOLDER_KEYS}
    effective_folders.update({name: value for name, value in updates.items() if name in FOLDER_KEYS})
    _check_distinct_folders(effective_folders)
    effective_sheets = {name: os.environ.get(name, "").strip() for name in OPTIONAL_KEYS}
    effective_sheets.update({name: value for name, value in updates.items() if name in OPTIONAL_KEYS})
    _check_distinct_sheets(effective_sheets)

    os.environ.update(updates)
    return tuple(updates)


def save_from_environment(path: Path = LOCAL_CONFIG) -> tuple[str, ...]:
    path = Path(path)
    if path.exists():
        raise FileExistsError("config/.env ja existe; edite-o sem substituir seus IDs.")

    absent = [name for name in FOLDER_KEYS if not os.environ.get(name, "").strip()]
    if absent:
        raise ValueError("Variaveis ainda ausentes no terminal: " + ", ".join(absent))

    values = {name: _valid_id(name, os.environ[name]) for name in FOLDER_KEYS}
    for name in OPTIONAL_KEYS:
        optional = os.environ.get(name, "").strip()
        if optional:
            values[name] = _valid_id(name, optional)
    _check_distinct_folders(values)
    _check_distinct_sheets(values)

    content = (
        "# Configuracao LOCAL KE24. Nao enviar ao GitHub.\n"
        "# O arquivo prevalece sobre variaveis de ambiente para estes IDs.\n"
        + "".join(f"{name}={value}\n" for name, value in values.items())
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return tuple(values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configuracao local do KE24")
    parser.add_argument(
        "--save-from-env", action="store_true", required=True,
        help="Salva os IDs ja presentes no terminal no arquivo local ignorado pelo Git",
    )
    parser.parse_args(argv)
    try:
        names = save_from_environment()
    except (ValueError, FileExistsError, OSError) as exc:
        parser.exit(1, f"[FALHA] {exc}\n")
    print("[OK] config/.env criado com IDs configurados (valores omitidos).")
    print("Variaveis salvas: " + ", ".join(names))
    print("Nenhum token, chave HMAC ou credencial OAuth foi incluido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
