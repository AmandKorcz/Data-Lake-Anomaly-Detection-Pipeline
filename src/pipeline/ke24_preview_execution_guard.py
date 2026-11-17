"""Controle local da previa KE24: exclusao mutua e auditoria SEM dados KE24.

Nao e um lock distribuido do Google Drive. Nunca publicar dados neste modulo.
O diretorio data/curated/ ja e ignorado pelo Git neste repositorio.
"""

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

LOCK_NAME = ".ke24_preview.lock"
REPORT_NAME = "ke24_preview_last_run.json"
REPORT_SCHEMA_VERSION = 1
ALLOWED_STATUSES = {"SUCCESS", "BLOCKED", "FAILED", "INTERRUPTED"}
SAFE_METRICS = (
    "processed_rows",
    "train_rows",
    "validation_rows",
    "candidate_count",
    "stable_all_seeds",
    "history_existing",
    "history_new",
    "sheet_rows",
    "classification_total",
    "classification_pending",
    "classification_experimental",
    "classification_ready",
    "priority_high",
    "priority_medium",
    "priority_low",
)


class PreviewAlreadyRunning(RuntimeError):
    """Existe uma previa em andamento (ou lock de execucao interrompida)."""


def control_directory(root: Path) -> Path:
    return Path(root) / "data" / "curated"


def report_file(root: Path) -> Path:
    return control_directory(root) / REPORT_NAME


@contextmanager
def exclusive_preview(root: Path, run_id: str):
    """Lock atomico no mesmo diretorio de trabalho, sem remover locks alheios.

    Um lock deixado apos encerramento abrupto exige verificacao manual; nao
    usamos limpeza automatica por idade, porque uma analise pode ser longa.
    """
    directory = control_directory(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOCK_NAME
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise PreviewAlreadyRunning(
            "Outra previa pode estar em execucao; verifique o lock local antes de repetir."
        ) from None

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"run_id": run_id, "pid": os.getpid()}, stream)
            stream.write("\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise

    try:
        yield
    finally:
        # Nao apagar acidentalmente um lock que foi trocado por outro processo.
        try:
            owner = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("Lock de previa alterado/ausente; verificacao manual necessaria.") from exc
        if owner.get("run_id") != run_id or owner.get("pid") != os.getpid():
            raise RuntimeError("Propriedade do lock mudou; nao remover automaticamente.")
        path.unlink()


def _safe_metrics(result):
    if not isinstance(result, dict):
        return {}
    return {
        name: result[name]
        for name in SAFE_METRICS
        if type(result.get(name)) is int and result[name] >= 0
    }


def write_preview_report(
    root: Path, *, run_id: str, started_at: datetime, duration_seconds: float,
    status: str, result=None, failure_kind: str | None = None,
):
    """Sobrescreve apenas o ultimo status; jamais salva arquivos, IDs ou valores.

    O arquivo temporario e criado exclusivamente e substituido atomicamente
    pelo ultimo status, dentro de data/curated (ignorado pelo Git).
    """
    if status not in ALLOWED_STATUSES:
        raise ValueError("Status da previa nao suportado.")
    if not run_id or not run_id.isalnum():
        raise ValueError("Run ID invalido.")

    directory = control_directory(root)
    directory.mkdir(parents=True, exist_ok=True)
    finished = datetime.now(timezone.utc)
    content = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "mode": "preview_readonly",
        "status": status,
        "started_at_utc": started_at.isoformat(timespec="seconds"),
        "finished_at_utc": finished.isoformat(timespec="seconds"),
        "duration_seconds": round(max(0.0, duration_seconds), 3),
        "metrics": _safe_metrics(result),
        "failure_kind": failure_kind if failure_kind in {
            "Preflight", "PreviewBlocked", "RuntimeError", "Exception", "KeyboardInterrupt"
        } else None,
    }
    target = report_file(root)
    temporary = directory / f".{REPORT_NAME}.{run_id}.tmp"
    try:
        # Sem dados protegidos; ainda assim exige permissao restrita quando suportada.
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(content, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def read_preview_report(root: Path):
    path = report_file(root)
    if not path.exists():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ValueError("Relatorio de previa local incompativel.")
    if document.get("status") not in ALLOWED_STATUSES:
        raise ValueError("Status armazenado invalido.")
    return document
