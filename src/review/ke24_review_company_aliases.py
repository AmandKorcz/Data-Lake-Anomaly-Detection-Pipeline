from __future__ import annotations

import json
import os
import re
from pathlib import Path

from src.privacy.anonymize_ke24 import generate_token
from src.review.ke24_review_context import ALIAS_PATTERN

ROOT = Path(__file__).resolve().parents[2]
_CODE = re.compile(r"[A-Za-z0-9_.-]{1,32}\Z")
MAX_COMPANIES = 100


def alias_file_path():
    return Path.home() / "AppData/Local/KE24/review/company_aliases.json"


def _private_path(path):
    target = Path(path).expanduser().resolve()
    if target.is_relative_to(ROOT.resolve()):
        raise ValueError("O mapeamento privado deve permanecer fora do repositorio.")
    return target


def create_empty_alias_file(*, path=None):
    target = _private_path(path if path is not None else alias_file_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({"schema_version": 1, "companies": []}, out, indent=2)
            out.write("\n")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def load_private_company_aliases(key, *, path=None):
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("Chave KE24 invalida para tokenizacao de empresas.")
    target = _private_path(path if path is not None else alias_file_path())
    if not target.is_file() or target.stat().st_size > 32768:
        raise ValueError("Mapeamento privado ausente ou acima do limite.")
    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError("Mapeamento privado invalido.") from None
    if (not isinstance(doc, dict) or set(doc) != {"schema_version", "companies"}
            or type(doc["schema_version"]) is not int or doc["schema_version"] != 1
            or not isinstance(doc["companies"], list)
            or not 1 <= len(doc["companies"]) <= MAX_COMPANIES):
        raise ValueError("Contrato do mapeamento privado invalido ou sem empresas.")
    aliases = {}
    labels = set()
    for item in doc["companies"]:
        if not isinstance(item, dict) or set(item) != {"company_code", "company_label"}:
            raise ValueError("Entrada de empresa fora do contrato.")
        code, label = item["company_code"], item["company_label"]
        if (not isinstance(code, str) or not _CODE.fullmatch(code)
                or not isinstance(label, str) or not ALIAS_PATTERN.fullmatch(label)
                or label.startswith(("=", "+", "-", "@"))):
            raise ValueError("Codigo ou alias empresarial privado invalido.")
        token = generate_token(code, "company_code", code, key)
        if token in aliases or label.casefold() in labels:
            raise ValueError("Empresa ou alias duplicado no mapeamento privado.")
        aliases[token] = label
        labels.add(label.casefold())
    return aliases
