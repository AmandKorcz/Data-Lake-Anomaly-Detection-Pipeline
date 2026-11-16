import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGGER = logging.getLogger("ke24.orchestrator")

STEPS = (
    (
        "Ingestao Google Drive",
        (
            "src/ingestion/google_drive_client.py",
            "src/ingestion/ke24_drive_inventory.py",
            "src/ingestion/prepare_ke24.py",
        ),
    ),
    (
        "Validacao de qualidade",
        (
            "src/quality/validate_ke24.py",
            "config/ke24_schema.json",
            "config/ke24_quality_rules.json",
            "config/ke24_privacy_policy.json",
        ),
    ),
    ("Camada processed", ("src/processing/prepare_ke24_processed.py",)),
    ("Deteccao de anomalias", ("src/pipeline/run_ke24_pretrained_inference.py",)),
    (
        "Persistencia de alertas",
        (
            "src/alerts/build_ke24_pretrained_outputs.py",
            "src/alerts/ke24_pretrained_csv_transaction.py",
        ),
    ),
    ("Google Sheets", ("src/loading/publish_ke24_google_sheets.py",)),
)

# Sao artefatos do modelo existente, nao fontes XLSX para ingestao.
ARTIFACTS = (
    "data/models/ke24_temporal",
    "data/curated/ke24_ml_feature_manifest.csv",
    "data/curated/ke24_validation_features.parquet",
)


def inspect_project(root: Path) -> dict:
    root = Path(root)
    checks = []
    for stage, dependencies in STEPS:
        missing = [rel for rel in dependencies if not (root / rel).is_file()]
        checks.append({"stage": stage, "ok": not missing, "missing": missing})
    return {
        "steps": checks,
        "structural_ok": all(check["ok"] for check in checks),
        "artifacts": {rel: (root / rel).exists() for rel in ARTIFACTS},
    }


def run_dry_run(root: Path = PROJECT_ROOT) -> int:
    LOGGER.info("KE24 dry-run | run_id=%s | inicio_utc=%s", uuid4().hex[:12],
                datetime.now(timezone.utc).isoformat(timespec="seconds"))
    report = inspect_project(root)
    for check in report["steps"]:
        if check["ok"]:
            LOGGER.info("[OK] %s: componentes encontrados", check["stage"])
        else:
            LOGGER.error("[FALHA] %s: componentes ausentes: %s",
                         check["stage"], ", ".join(check["missing"]))
    LOGGER.info("Fonte das extracoes: Google Drive (nao data/raw)")
    LOGGER.info("Pasta do Drive configurada: %s",
                "SIM" if os.environ.get("KE24_DRIVE_FOLDER_ID") else "NAO")
    for rel, exists in report["artifacts"].items():
        LOGGER.info("Artefato ML %s: %s (conteudo nao validado)",
                    rel, "PRESENTE" if exists else "AUSENTE")
    LOGGER.warning("BigQuery: gravacao nao autorizada pelo orquestrador")
    LOGGER.warning("Google Sheets: publicacao desabilitada")
    LOGGER.info("Dry-run concluido sem abrir dados nem acessar APIs")
    return 0 if report["structural_ok"] else 1


def run_drive_check() -> int:
    from src.ingestion.ke24_drive_inventory import (
        DriveInventoryError,
        configured_folder_id,
        inspect_drive_folder,
    )

    LOGGER.info("KE24 drive-check | run_id=%s", uuid4().hex[:12])
    if not inspect_project(PROJECT_ROOT)["structural_ok"]:
        LOGGER.error("Componentes da orquestracao ausentes; consulta bloqueada")
        return 1

    try:
        folder_id = configured_folder_id()
        # Reutiliza a conexao OAuth do projeto, sem ampliar permissoes.
        from googleapiclient.discovery import build
        from src.ingestion.google_drive_client import authenticate

        credentials = authenticate()
        service = build("drive", "v3", credentials=credentials,
                        cache_discovery=False)
        report = inspect_drive_folder(service, folder_id)
    except DriveInventoryError as exc:
        LOGGER.error("[FALHA] %s", exc)
        return 1
    except Exception:
        LOGGER.error("[FALHA] Autenticacao ou conexao com Drive indisponivel")
        return 1

    LOGGER.info("[OK] Arquivos XLSX no Drive: %d (nomes e IDs omitidos)",
                report["files_count"])
    LOGGER.info("[OK] Conteudos distintos: %d", report["unique_content_count"])
    if report["same_content_files"]:
        LOGGER.warning("Arquivos com conteudo repetido: %d; revisar antes de ingerir",
                       report["same_content_files"])
    if not report["files_count"]:
        LOGGER.error("Nenhum XLSX na pasta do Drive configurada")
    LOGGER.warning("Nenhuma extracao foi baixada ou processada nesta etapa")
    LOGGER.warning("Nenhum dado foi enviado ao BigQuery ou Google Sheets")
    return 0 if report["safe_for_selection"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Orquestracao KE24 com origem no Drive")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true",
                       help="Verifica componentes locais sem conectar ao Drive")
    modes.add_argument("--drive-check", action="store_true",
                       help="Verifica metadados dos XLSX da pasta Drive configurada")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        stream=sys.stdout,
    )
    if args.dry_run:
        return run_dry_run()
    if args.drive_check:
        return run_drive_check()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
