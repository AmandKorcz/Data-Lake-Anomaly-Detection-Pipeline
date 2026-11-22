from __future__ import annotations

from collections import Counter, defaultdict

import pandas as pd

from src.review.ke24_review_recurrence import (
    _business_key,
    _protected,
    _provably_distinct_from_incomplete_history,
    _validate_frame,
    plan_business_recurrences,
)


def summarize_ambiguous_recurrences(history: pd.DataFrame, current: pd.DataFrame) -> dict:
    _validate_frame(history, "Historico")
    _validate_frame(current, "Candidatos")

    historical = history.to_dict(orient="records")
    candidates = current.to_dict(orient="records")
    old_ids = set(history["alert_id"])
    old_key_counts = Counter()
    current_key_counts = Counter()
    unknown_old_scopes: dict[tuple, list] = defaultdict(list)
    old_missing_document = 0

    for row in historical:
        key = _business_key(row)
        if key is None:
            scope = (row["company_code"], row["period_key"])
            unknown_old_scopes[scope].append(row)
            if _protected(row["reference_document"], required=False) is None:
                old_missing_document += 1
        else:
            old_key_counts[key] += 1

    for row in candidates:
        key = _business_key(row)
        if key is not None:
            current_key_counts[key] += 1

    reasons = Counter()
    partial_exact_match = 0
    partial_different = 0
    partial_inconclusive = 0
    candidates_historic_missing_document = 0
    for row in candidates:
        if row["alert_id"] in old_ids:
            continue
        key = _business_key(row)
        if key is None:
            continue  # Incomplete current keys have separate unmatchable treatment.
        scope = (row["company_code"], row["period_key"])
        if scope in unknown_old_scopes:
            old_incomplete = unknown_old_scopes[scope]
            if (key not in old_key_counts and current_key_counts[key] == 1
                    and _provably_distinct_from_incomplete_history(row, old_incomplete)):
                reasons["historico_incompleto_distinto_por_proveniencia"] += 1
            else:
                reasons["historico_incompleto_mesmo_escopo"] += 1
            if any(_protected(x["reference_document"], required=False) is None
                   for x in old_incomplete):
                candidates_historic_missing_document += 1
            candidate_partial = _business_key(row, allow_missing_document=True)
            historical_partials = [
                _business_key(x, allow_missing_document=True) for x in old_incomplete
            ]
            if any(v is None for v in historical_partials):
                partial_inconclusive += 1
            elif any((v[0], *v[2:]) == (candidate_partial[0], *candidate_partial[2:])
                     for v in historical_partials):
                partial_exact_match += 1
            else:
                partial_different += 1
        elif key in old_key_counts and (
            old_key_counts[key] != 1 or current_key_counts[key] != 1
        ):
            reasons["chave_completa_nao_exclusiva"] += 1

    plan = plan_business_recurrences(
        history, current, {aid: "PENDING" for aid in old_ids}
    )
    derived_count = (reasons["historico_incompleto_mesmo_escopo"]
                     + reasons["chave_completa_nao_exclusiva"])
    if derived_count != plan.ambiguous_candidates:
        raise RuntimeError("Diagnostico e planejador divergem: revisao necessaria.")

    return {
        "historicos": len(historical),
        "candidatos": len(candidates),
        "ids_reconhecidos": int(current["alert_id"].isin(old_ids).sum()),
        "historicos_incompletos": sum(len(v) for v in unknown_old_scopes.values()),
        "historicos_sem_documento": old_missing_document,
        "escopos_historicos_incompletos": len(unknown_old_scopes),
        "ambiguos_total": plan.ambiguous_candidates,
        "ambiguidade_historico_incompleto": reasons["historico_incompleto_mesmo_escopo"],
        "distintos_por_proveniencia": reasons["historico_incompleto_distinto_por_proveniencia"],
        "ambiguidade_chave_nao_exclusiva": reasons["chave_completa_nao_exclusiva"],
        "ambiguos_em_escopo_com_historico_sem_documento": candidates_historic_missing_document,
        "chave_parcial_igual_sem_documento": partial_exact_match,
        "chave_parcial_diferente_sem_documento": partial_different,
        "chave_parcial_inconclusiva": partial_inconclusive,
        "candidatos_sem_chave_completa": plan.unmatchable_candidates,
        "candidatos_sem_chave_liberados_por_escopo": plan.safe_unmatchable_candidates,
        "bloqueios_totais": plan.publication_blockers,
    }


def print_summary(s: dict) -> None:
    print("\nKE24 - Diagnostico pontual das ambiguidades (SOMENTE LEITURA)")
    for label, key in (
        ("Alertas historicos", "historicos"),
        ("Candidatos atuais", "candidatos"),
        ("IDs ja reconhecidos", "ids_reconhecidos"),
        ("Alertas historicos com chave incompleta", "historicos_incompletos"),
        ("Destes, sem documento protegido", "historicos_sem_documento"),
        ("Grupos empresa/competencia com historico incompleto", "escopos_historicos_incompletos"),
        ("Total de ambiguidades", "ambiguos_total"),
        ("Ambiguidades por historico incompleto no mesmo grupo", "ambiguidade_historico_incompleto"),
        ("Distintos por origem comprovada (sem suprimir)", "distintos_por_proveniencia"),
        ("Ambiguidades por chave completa nao exclusiva", "ambiguidade_chave_nao_exclusiva"),
        ("Candidatos em grupo com historico sem documento", "ambiguos_em_escopo_com_historico_sem_documento"),
        ("Chave parcial identica sem comparar documento", "chave_parcial_igual_sem_documento"),
        ("Chave parcial diferente sem comparar documento", "chave_parcial_diferente_sem_documento"),
        ("Comparacao parcial inconclusiva", "chave_parcial_inconclusiva"),
        ("Candidatos atuais com chave incompleta", "candidatos_sem_chave_completa"),
        ("Excecoes seguras existentes para chave incompleta", "candidatos_sem_chave_liberados_por_escopo"),
        ("Bloqueios totais para publicar", "bloqueios_totais"),
    ):
        print(f"{label}: {s[key]}")
    print("Comparacao parcial igual/diferente e apenas sinal diagnostico; nao autoriza liberar alertas.")
    print("Nenhum ID, valor financeiro, XLSX, manifesto, alerta ou planilha foi alterado.")


def main() -> int:
    from datetime import datetime, timezone
    from googleapiclient.discovery import build
    from tools.ke24_google_ipv4_compat import google_api_ipv4_only
    from src.ingestion.google_drive_client import authenticate
    from src.loading.ke24_drive_alert_history import load_alert_history_state
    from src.pipeline.run_ke24_drive_features import (
        build_features_in_memory, load_verified_processed_dataframe,
    )
    from src.pipeline.run_ke24_drive_alert_preview import build_alert_preview_in_memory
    from src.pipeline.run_ke24_drive_alert_outputs import build_pending_alert_outputs_in_memory
    from src.privacy.privacy_runtime import load_privacy_runtime

    print("KE24 - Leitura agregada em modo somente leitura", flush=True)
    try:
        with google_api_ipv4_only():
            policy, schema, _ = load_privacy_runtime()
            service = build("drive", "v3", credentials=authenticate(), cache_discovery=False)
            history = load_alert_history_state(service, policy).history
            processed = load_verified_processed_dataframe(service, policy, schema)
            feature_dataset, feature_manifest, _ = build_features_in_memory(processed, policy)
            preview = build_alert_preview_in_memory(feature_dataset, feature_manifest, policy)
            current = build_pending_alert_outputs_in_memory(
                preview["candidates"], policy, datetime.now(timezone.utc)
            )["history"]
            summary = summarize_ambiguous_recurrences(history, current)
        print_summary(summary)
        return 0
    except Exception as exc:
        print(f"Diagnostico interrompido ({type(exc).__name__}). Nenhuma escrita foi solicitada.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
