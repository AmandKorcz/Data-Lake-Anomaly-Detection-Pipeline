from __future__ import annotations

import hashlib
import hmac
import json
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from src.review.ke24_review_sheet import validate_review_sheet_matrix

SNAPSHOT_VERSION = 1


def snapshot_path():
    return Path.home() / "AppData/Local/KE24/review/last_published_queue.json"


def _key_bytes(key):
    if not isinstance(key, bytes) or len(key) < 16:
        raise ValueError("Chave para assinatura local invalida.")
    return key


def _payload(spreadsheet_id, sheet_id, rows):
    if not isinstance(spreadsheet_id, str) or not spreadsheet_id:
        raise ValueError("ID da planilha ausente.")
    if type(sheet_id) is not int or sheet_id < 0:
        raise ValueError("ID da aba invalido.")
    validate_review_sheet_matrix(rows)
    return {
        "schema_version": SNAPSHOT_VERSION,
        "spreadsheet_id": spreadsheet_id,
        "sheet_id": sheet_id,
        "rows": rows,
    }


def _digest(key, payload):
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    derived_key = hmac.new(_key_bytes(key), b"KE24_REVIEW_SNAPSHOT_V1", hashlib.sha256).digest()
    return hmac.new(derived_key, raw, hashlib.sha256).hexdigest()


def signed_document(key, spreadsheet_id, sheet_id, rows):
    payload = _payload(spreadsheet_id, sheet_id, rows)
    return {**payload, "signature": _digest(key, payload)}


def verify_document(key, document, spreadsheet_id, sheet_id):
    if not isinstance(document, dict) or set(document) != {
        "schema_version", "spreadsheet_id", "sheet_id", "rows", "signature",
    }:
        raise ValueError("Snapshot local invalido ou alterado.")
    signature = document["signature"]
    if not isinstance(signature, str) or len(signature) != 64:
        raise ValueError("Assinatura do snapshot invalida.")
    payload = _payload(document["spreadsheet_id"], document["sheet_id"], document["rows"])
    if (payload["schema_version"] != SNAPSHOT_VERSION
            or document["spreadsheet_id"] != spreadsheet_id
            or document["sheet_id"] != sheet_id
            or not hmac.compare_digest(signature, _digest(key, payload))):
        raise ValueError("Snapshot local nao confiavel; reconcilie manualmente.")
    return payload["rows"]


def load_snapshot(key, spreadsheet_id, sheet_id, *, path=None):
    target = snapshot_path() if path is None else Path(path)
    if not target.is_file():
        raise RuntimeError("Snapshot confiavel ausente; publicacao bloqueada.")
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
        return verify_document(key, document, spreadsheet_id, sheet_id)
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Snapshot local ilegivel ou adulterado; nao publicar.") from None


def save_snapshot(key, spreadsheet_id, sheet_id, rows, *, path=None):
    target = snapshot_path() if path is None else Path(path)
    document = signed_document(key, spreadsheet_id, sheet_id, rows)
    target.parent.mkdir(parents=True, exist_ok=True)
    # No cleartext records; still keep private because alert IDs are pseudonymous.
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


@contextmanager
def exclusive_review_writer(*, path=None):
    target = (snapshot_path().parent / ".review_sync.lock") if path is None else Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    owner = uuid4().hex
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise RuntimeError("Outra sincronizacao pode estar ativa; verifique lock local.") from None
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(owner)
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        try:
            observed = target.read_text(encoding="ascii")
        except OSError:
            raise RuntimeError("Lock de sincronizacao ausente; verificar manualmente.") from None
        if observed != owner:
            raise RuntimeError("Lock foi alterado; nao remover automaticamente.")
        target.unlink()
