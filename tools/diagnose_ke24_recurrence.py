from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation

import pandas as pd


STAGES = (
    ("empresa + competencia", ("company_code", "period_key")),
    ("+ documento protegido", ("company_code", "period_key", "reference_document")),
    ("+ dimensoes da ocorrencia", (
        "company_code", "period_key", "reference_document",
        "profit_center", "product", "currency", "currency_type",
    )),
    ("+ tipo de sinal", (
        "company_code", "period_key", "reference_document",
        "profit_center", "product", "currency", "currency_type",
        "dominant_measure", "signal_method",
    )),
    ("+ valores da ocorrencia (chave 7.6)", (
        "company_code", "period_key", "reference_document",
        "profit_center", "product", "currency", "currency_type",
        "dominant_measure", "signal_method", "total_absolute_value",
        "dominant_measure_value",
    )),
)
_OPTIONAL = frozenset({"profit_center", "product"})
_NUMERIC = frozenset({"currency_type", "total_absolute_value", "dominant_measure_value"})


def _normalized(value, field):
    if value is None or pd.isna(value):
        return None
    if field in _NUMERIC:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
        return number.normalize() if number.is_finite() else None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return cleaned.upper() if field == "currency" else cleaned


def _counts(frame: pd.DataFrame, columns: tuple[str, ...]) -> tuple[Counter, int]:
    result = Counter()
    incomplete = 0
    for row in frame.loc[:, list(columns)].itertuples(index=False, name=None):
        key = tuple(_normalized(value, field) for field, value in zip(columns, row))
        if any(value is None for field, value in zip(columns, key) if field not in _OPTIONAL):
            incomplete += 1
            continue
        result[key] += 1
    return result, incomplete


def summarize(history: pd.DataFrame, current: pd.DataFrame):
    required = {"alert_id", "_source_file_sha256", *(field for _, cols in STAGES for field in cols)}
    for frame in (history, current):
        if not isinstance(frame, pd.DataFrame):
            raise ValueError("Conjunto de dados invalido")
        if missing := required.difference(frame.columns):
            raise ValueError("Campos ausentes: " + ", ".join(sorted(missing)))
    old_ids = set(history["alert_id"].tolist())
    new_ids = set(current["alert_id"].tolist())
    old_files = set(history["_source_file_sha256"].dropna().tolist())
    new_files = set(current["_source_file_sha256"].dropna().tolist())
    stats = {
        "historico": len(history),
        "candidatos": len(current),
        "alert_ids_em_comum": len(old_ids & new_ids),
        "arquivos_origem_em_comum": len(old_files & new_files),
        "etapas": [],
    }
    for label, fields in STAGES:
        old, old_incomplete = _counts(history, fields)
        new, new_incomplete = _counts(current, fields)
        keys = old.keys() & new.keys()
        stats["etapas"].append({
            "etapa": label,
            "chaves_compartilhadas": len(keys),
            "candidatos_com_alguma_correspondencia": sum(new[k] for k in keys),
            "candidatos_em_correspondencia_1_para_1": sum(
                new[k] for k in keys if old[k] == new[k] == 1
            ),
            "historicos_com_campos_insuficientes": old_incomplete,
            "candidatos_com_campos_insuficientes": new_incomplete,
        })
    return stats


def print_summary(stats):
    print("\nKE24 7.6 — Diagnostico agregado (SEM GRAVACAO)\n")
    print(f"Alertas historicos: {stats['historico']}")
    print(f"Candidatos atuais: {stats['candidatos']}")
    print(f"alert_id em comum: {stats['alert_ids_em_comum']}")
    print(f"Arquivos de origem em comum: {stats['arquivos_origem_em_comum']}")
    print("\nEtapas de comparacao (apenas contagens):")
    for i, step in enumerate(stats["etapas"], 1):
        print(f"{i}. {step['etapa']}")
        print(f"   Chaves compartilhadas: {step['chaves_compartilhadas']}")
        print(f"   Candidatos com alguma correspondencia: {step['candidatos_com_alguma_correspondencia']}")
        print(f"   Correspondencias unicas 1:1: {step['candidatos_em_correspondencia_1_para_1']}")
        print(f"   Sem campos suficientes (historico / atual): "
              f"{step['historicos_com_campos_insuficientes']} / "
              f"{step['candidatos_com_campos_insuficientes']}")
    print("\nSomente diagnostico: estas contagens nao autorizam suprimir ou publicar alertas.")


def main():
    from datetime import datetime, timezone
    from googleapiclient.discovery import build

    from src.ingestion.google_drive_client import authenticate
    from src.loading.ke24_drive_alert_history import load_alert_history_state
    from src.pipeline.run_ke24_drive_features import build_features_in_memory
    from src.pipeline.run_ke24_drive_features import load_verified_processed_dataframe
    from src.pipeline.run_ke24_drive_alert_preview import build_alert_preview_in_memory
    from src.pipeline.run_ke24_drive_alert_outputs import build_pending_alert_outputs_in_memory
    from src.privacy.privacy_runtime import load_privacy_runtime

    policy, schema, _ = load_privacy_runtime()
    service = build("drive", "v3", credentials=authenticate(), cache_discovery=False)
    previous = load_alert_history_state(service, policy).history
    processed = load_verified_processed_dataframe(service, policy, schema)
    features, manifest, _ = build_features_in_memory(processed, policy)
    preview = build_alert_preview_in_memory(features, manifest, policy)
    current = build_pending_alert_outputs_in_memory(
        preview["candidates"], policy, datetime.now(timezone.utc)
    )["history"]
    print_summary(summarize(previous, current))


if __name__ == "__main__":
    main()
