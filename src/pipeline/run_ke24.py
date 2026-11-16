import argparse
import logging
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

from src.config.ke24_local_settings import load_ke24_local_settings

# Um unico arquivo local para o CLI; os dados nunca sao exibidos nos logs.
load_ke24_local_settings()

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger("ke24.orchestrator")

# Mantem o contrato do dry-run e dos testes anteriores.
STEPS = (
    ("Ingestao incremental pelo Google Drive", "src/pipeline/run_ke24_drive_ingestion.py"),
    ("Features a partir da camada processed", "src/pipeline/run_ke24_drive_features.py"),
    ("Modelos e deteccao", "src/pipeline/run_ke24_drive_model.py"),
    ("Previa dos candidatos", "src/pipeline/run_ke24_drive_alert_preview.py"),
    ("Sincronizacao com Sheets", "src/pipeline/run_ke24_drive_looker_sync.py"),
)
ENV_SOURCE = "KE24_DRIVE_RAW_FOLDER_ID"
PREVIEW_ENV = (
    ENV_SOURCE,
    "KE24_DRIVE_PROCESSED_FOLDER_ID",
    "KE24_DRIVE_ALERTS_FOLDER_ID",
)
PREVIEW_FILES = (
    "src/pipeline/run_ke24_drive_alert_outputs.py",
    "src/loading/ke24_drive_manifest.py",
    "src/loading/ke24_drive_alert_history.py",
    "src/loading/ke24_looker_sheet.py",
)


class PreviewBlocked(RuntimeError):
    """Bloqueio esperado (sem publicar ou expor identificadores)."""


def inspect_cloud_flow(root=ROOT, environ=None):
    root = Path(root)
    env = os.environ if environ is None else environ
    steps = tuple(
        {"name": label, "path": relative, "available": (root / relative).is_file()}
        for label, relative in STEPS
    )
    return {
        "steps": steps,
        "source_configured": bool(env.get(ENV_SOURCE, "").strip()),
        "ready_for_review": all(step["available"] for step in steps)
        and bool(env.get(ENV_SOURCE, "").strip()),
    }


def run_dry_run(root=ROOT, environ=None):
    report = inspect_cloud_flow(root, environ)
    LOG.info("KE24 | dry-run | run_id=%s", uuid4().hex[:12])
    LOG.info("Origem oficial: Google Drive, pasta raw ja configurada")
    LOG.info(
        "Configuracao %s: %s",
        ENV_SOURCE,
        "DETECTADA" if report["source_configured"] else "INDISPONIVEL NESTE PROCESSO",
    )
    for step in report["steps"]:
        LOG.info(
            "[%s] %s (%s)",
            "OK" if step["available"] else "PENDENTE",
            step["name"],
            step["path"],
        )
    LOG.warning("Somente inventario dos componentes; sem conexoes, processamento ou publicacao")
    LOG.info("Ingestao incremental e manifesto existentes serao reutilizados")
    LOG.info("Nenhum novo treino ou publicacao sera disparado automaticamente")
    return 0 if report["ready_for_review"] else 1


@contextmanager
def _timed_stage(label):
    LOG.info("[INICIO] %s", label)
    started = perf_counter()
    try:
        yield
    except Exception:
        LOG.error("[FALHA] %s | duracao=%.2fs", label, perf_counter() - started)
        raise
    else:
        LOG.info("[OK] %s | duracao=%.2fs", label, perf_counter() - started)


def audit_drive_sources(
    service,
    secret_key,
    *,
    list_raw=None,
    download=None,
    read_manifest=None,
    calculate_hash=None,
    tokenize=None,
):
    """Confere hashes do raw (em memoria) com arquivos commitados no manifesto.

    Nunca devolve nomes, IDs ou conteudo; so contagens. Se houver arquivo novo,
    bloqueia a inferencia para nao usar apenas uma base processed antiga.
    """
    if list_raw is None or download is None:
        from src.ingestion.google_drive_client import (
            list_raw_xlsx_files,
            read_xlsx_from_drive,
        )
        list_raw = list_raw or list_raw_xlsx_files
        download = download or read_xlsx_from_drive
    if read_manifest is None:
        from src.loading.ke24_drive_manifest import load_processed_manifest
        read_manifest = load_processed_manifest
    if calculate_hash is None:
        from src.ingestion.prepare_ke24 import calculate_file_hash
        calculate_hash = calculate_file_hash
    if tokenize is None:
        from src.privacy.anonymize_ke24 import generate_token
        tokenize = generate_token

    source_files = list_raw(service)
    if not source_files:
        raise PreviewBlocked("Pasta raw do Drive sem arquivos XLSX; nao ha base para comparar.")

    manifest = read_manifest(service)
    if manifest.empty:
        raise PreviewBlocked("Manifesto processed vazio; carregamento revisado necessario.")

    FILE_ID = "_source_file_sha256"
    if FILE_ID not in manifest.columns or manifest[FILE_ID].isna().any():
        raise PreviewBlocked("Manifesto processed invalido; revisao necessaria.")
    committed = set(manifest[FILE_ID].astype(str))
    raw_tokens = set()

    for source in source_files:
        with download(service, source["id"]) as buffer:
            digest = calculate_hash(buffer)
        protected_hash = tokenize(
            value=digest,
            column=FILE_ID,
            company=None,
            secret_key=secret_key,
        )
        if protected_hash in raw_tokens:
            raise PreviewBlocked("Conteudo XLSX duplicado no raw; revisao necessaria.")
        raw_tokens.add(protected_hash)

    missing = raw_tokens - committed
    if missing:
        raise PreviewBlocked(
            f"{len(missing)} extracao(oes) do raw ainda fora do manifesto processed. "
            "Revisar ingestao incremental antes de gerar novas analises."
        )
    return {"raw_files": len(source_files), "processed_files": len(committed)}


def _live_adapters():
    # Importados apenas no modo --preview, que usa OAuth e dados remotos.
    from src.pipeline.run_ke24_drive_features import (
        build_features_in_memory,
        load_verified_processed_dataframe,
    )
    from src.pipeline.run_ke24_drive_alert_preview import build_alert_preview_in_memory
    from src.pipeline.run_ke24_drive_alert_outputs import build_pending_alert_outputs_in_memory
    from src.loading.ke24_drive_alert_history import (
        append_new_alerts,
        load_alert_history_state,
    )
    from src.loading.ke24_looker_sheet import build_matrix

    return SimpleNamespace(
        load_processed=load_verified_processed_dataframe,
        build_features=build_features_in_memory,
        build_alert_preview=build_alert_preview_in_memory,
        build_pending_outputs=build_pending_alert_outputs_in_memory,
        load_history=load_alert_history_state,
        append_alerts=append_new_alerts,
        sheet_matrix=build_matrix,
    )


def execute_readonly_stages(
    service, privacy_policy, schema, secret_key, *, adapters=None, source_audit=None
):
    """Uma unica leitura do processed e uma unica previa de ML.

    ``build_alert_preview_in_memory`` ja treina/avalia os modelos; nao chamar
    ``run_ke24_drive_model.main`` em paralelo (isso repetiria o treino).
    """
    adapters = adapters or _live_adapters()
    source_audit = source_audit or audit_drive_sources

    with _timed_stage("Conferencia raw x manifesto processed"):
        source_summary = source_audit(service, secret_key)
    LOG.info(
        "Origem conferida: %d arquivos raw; %d extracoes commitadas no processed",
        source_summary["raw_files"], source_summary["processed_files"],
    )

    with _timed_stage("Leitura verificada do processed no Drive"):
        processed = adapters.load_processed(service, privacy_policy, schema)

    with _timed_stage("Feature engineering em memoria"):
        dataset, feature_manifest, stats = adapters.build_features(processed, privacy_policy)

    with _timed_stage("Modelo e previa de candidatos (treino temporal em memoria)"):
        preview = adapters.build_alert_preview(dataset, feature_manifest, privacy_policy)

    with _timed_stage("Historico de alertas - simulacao, sem persistir"):
        current = adapters.build_pending_outputs(
            preview["candidates"], privacy_policy, datetime.now(timezone.utc)
        )["history"]
        previous = adapters.load_history(service, privacy_policy)
        combined, newly_detected = adapters.append_alerts(
            previous.history, current, privacy_policy
        )

    with _timed_stage("Layout para Sheets/Looker - somente em memoria"):
        matrix = adapters.sheet_matrix(combined, privacy_policy) if not combined.empty else []

    result = {
        "source": source_summary,
        "processed_rows": len(processed),
        "train_rows": stats["train_rows"],
        "validation_rows": stats["validation_rows"],
        "candidate_count": preview["candidate_count"],
        "stable_all_seeds": preview["stable_all_seeds"],
        "history_existing": len(previous.history),
        "history_new": newly_detected,
        "sheet_rows": max(0, len(matrix) - 1),
    }
    LOG.info(
        "RESUMO: processed=%d, treino=%d, validacao=%d, candidatos=%d, "
        "estaveis=%d, alertas_novos=%d, linhas_previstas_Sheets=%d",
        result["processed_rows"], result["train_rows"], result["validation_rows"],
        result["candidate_count"], result["stable_all_seeds"],
        result["history_new"], result["sheet_rows"],
    )
    return result


def _open_read_only_drive():
    from googleapiclient.discovery import build
    from src.ingestion.google_drive_client import authenticate
    from src.privacy.privacy_runtime import load_privacy_runtime

    policy, schema, secret_key = load_privacy_runtime()
    service = build(
        "drive", "v3", credentials=authenticate(), cache_discovery=False
    )
    return service, policy, schema, secret_key


def run_preview(root=ROOT, environ=None):
    env = os.environ if environ is None else environ
    LOG.info("KE24 | preview integrado SOMENTE LEITURA | run_id=%s", uuid4().hex[:12])
    report = inspect_cloud_flow(root, env)
    if not report["ready_for_review"]:
        LOG.error("Preflight incompleto; confira --dry-run antes de conectar ao Drive.")
        return 1
    if any(not (Path(root) / rel).is_file() for rel in PREVIEW_FILES):
        LOG.error("Componentes de historico ou exportacao ausentes; nenhuma conexao realizada.")
        return 1
    missing = [name for name in PREVIEW_ENV if not env.get(name, "").strip()]
    if missing:
        LOG.error("Variaveis ausentes neste processo: %s", ", ".join(missing))
        return 1

    try:
        service, policy, schema, secret_key = _open_read_only_drive()
        execute_readonly_stages(service, policy, schema, secret_key)
    except PreviewBlocked as exc:
        LOG.error("BLOQUEADO: %s", exc)
        return 1
    except Exception as exc:
        # Sem stack trace nem IDs, valores financeiros ou dados de origem no log.
        LOG.error("[FALHA] Previa interrompida (%s). Verifique os logs do passo.", type(exc).__name__)
        return 1
    LOG.info("Concluido: sem publicar no Drive/Sheets/BigQuery nem persistir dados/modelos locais.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="KE24: orquestracao com origem oficial no Google Drive")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true", help="Preflight local, sem conexoes")
    modes.add_argument(
        "--preview", action="store_true",
        help="Consulta Drive e executa ML/historico/planilha em memoria; NAO publica",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    return run_dry_run() if args.dry_run else run_preview()


if __name__ == "__main__":
    raise SystemExit(main())
