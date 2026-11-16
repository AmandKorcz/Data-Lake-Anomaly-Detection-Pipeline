from datetime import datetime, timezone

import pandas as pd
from googleapiclient.discovery import build

from src.alerts.build_ke24_pretrained_outputs import build_pretrained_outputs
from src.ingestion.google_drive_client import authenticate
from src.ml.build_ke24_pretrained_alert_candidates import (
    validate_pretrained_materiality,
)
from src.ml.ke24_record_identity import validate_record_identity
from src.pipeline.run_ke24_drive_alert_preview import (
    build_alert_preview_in_memory,
)
from src.pipeline.run_ke24_drive_features import (
    build_features_in_memory,
    load_verified_processed_dataframe,
)
from src.privacy.privacy_runtime import load_privacy_runtime


PENDING_CLASSIFICATION_STATUS = "PENDING_BUSINESS_RULE"
PENDING_ALERT_REASON = "classification_rules_not_approved"


def build_pending_alert_outputs_in_memory(
    candidates,
    privacy_policy,
    detected_at_utc,
    *,
    previous_history=None,
):

    if not isinstance(candidates, pd.DataFrame):
        raise ValueError("Candidatos precisam ser um DataFrame.")

    if candidates.columns.duplicated().any():
        raise ValueError("Colunas duplicadas nos candidatos.")

    # Uma prévia não pode encobrir uma classificação anterior já realizada.
    already_classified = {"alert_level", "classification_status", "alert_reason"}
    if already_classified.intersection(candidates.columns):
        raise ValueError("A entrada já contém campos de classificação de alertas.")

    validate_record_identity(candidates, allow_empty=True)
    if not candidates.empty:
        validate_pretrained_materiality(candidates)

    pending = candidates.copy()
    pending["alert_level"] = "PENDING"
    pending["classification_status"] = PENDING_CLASSIFICATION_STATUS
    pending["alert_reason"] = PENDING_ALERT_REASON

    history, dashboard = build_pretrained_outputs(
        pending,
        previous_history,
        detected_at_utc,
        privacy_policy,
    )

    if (
        len(history) < len(pending)
        or len(dashboard) != len(history)
        or history["alert_id"].duplicated().any()
    ):
        raise RuntimeError("Histórico ou dashboard inconsistente.")

    return {
        "current_candidates": len(pending),
        "history": history,
        "dashboard": dashboard,
        "pending_current": len(pending),
    }


def main():
    print("\nKE24 - Drive processed -> Histórico e dashboard (PRÉVIA)\n")
    print("Modo somente leitura: sem gravação em disco, no Drive ou no Sheets.")
    print("Regras de exemplo NÃO são utilizadas para classificar dados reais.")

    privacy_policy, privacy_schema, _ = load_privacy_runtime()
    service = build(
        "drive", "v3", credentials=authenticate(), cache_discovery=False
    )
    processed = load_verified_processed_dataframe(
        service, privacy_policy, privacy_schema
    )
    dataset, feature_manifest, _ = build_features_in_memory(
        processed, privacy_policy
    )
    preview = build_alert_preview_in_memory(
        dataset, feature_manifest, privacy_policy
    )

    outputs = build_pending_alert_outputs_in_memory(
        preview["candidates"],
        privacy_policy,
        datetime.now(timezone.utc),
    )

    print("\nPrévia de histórico e dashboard concluída!")
    print(f"Registros de validação: {preview['validation_rows']}")
    print(f"Candidatos p95: {preview['candidate_count']}")
    print(f"Candidatos estáveis nas 5 seeds: {preview['stable_all_seeds']}")
    print(f"Alertas pendentes gerados: {outputs['pending_current']}")
    print(f"Entradas de histórico (em memória): {len(outputs['history'])}")
    print(f"Linhas para dashboard (em memória): {len(outputs['dashboard'])}")
    print("Classificação financeira: PENDENTE de regras de negócio aprovadas.")
    print("IDs de alerta estáveis gerados a partir de identificadores protegidos.")
    print("\nStatus: APROVADO como prévia técnica, sem publicação externa.")
    print("O histórico não persiste entre execuções deste comando.")
    print("Os candidatos ainda não são erros financeiros comprovados.")


if __name__ == "__main__":
    main()
