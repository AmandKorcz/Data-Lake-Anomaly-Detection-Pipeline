import argparse

import pandas as pd
from googleapiclient.discovery import build

from src.ingestion.google_drive_client import authenticate
from src.ml import profile_ke24_anomaly_materiality as materiality
from src.ml.build_ke24_pretrained_anomaly_results import (
    build_pretrained_anomaly_results,
)
from src.ml.build_ke24_pretrained_alert_candidates import (
    attach_pretrained_stability,
    build_pretrained_alert_candidates,
)
from src.ml.build_ke24_pretrained_stability import build_pretrained_stability
from src.ml.classify_ke24_pretrained_alerts import build_pretrained_classification
from src.ml.evaluate_ke24_isolation_forest_stability import RANDOM_STATES
from src.ml.ke24_grouped_model_runtime import train_grouped_models
from src.ml.ke24_record_identity import validate_record_identity
from src.ml.load_ke24_alert_rules import load_protected_alert_rules
from src.pipeline.run_ke24_drive_features import (
    build_features_in_memory,
    load_verified_processed_dataframe,
)
from src.pipeline.run_ke24_drive_model import PRIMARY_SEED, run_model_in_memory
from src.privacy.privacy_runtime import load_privacy_runtime

PREVIEW_MODEL_VERSION = "ke24_drive_preview_s42"


def build_alert_preview_in_memory(
    dataset,
    feature_manifest,
    privacy_policy,
    *,
    protected_rules=None,
):
    if protected_rules is not None and not isinstance(protected_rules, dict):
        raise ValueError("As regras protegidas precisam ser um dicionário.")

    # Reaproveita o treino temporal e a seed 42 da etapa anterior.
    base = run_model_in_memory(dataset, feature_manifest, privacy_policy)
    train_matrix = base["train_matrix"]
    train_context = base["train_context"]
    validation_matrix = base["validation_matrix"]
    validation_context = base["validation_context"]

    if PRIMARY_SEED not in RANDOM_STATES:
        raise RuntimeError("A seed principal não consta das seeds de estabilidade.")
    validate_record_identity(validation_context)

    # Contrato de versão apenas para a prévia em memória. Não salva modelos.
    primary = base["model"]
    model_manifest = {
        "model_version": PREVIEW_MODEL_VERSION,
        "threshold_method": "training_p95",
        "feature_columns": list(primary.feature_columns),
        "group_count": len(primary.models),
    }
    anomaly_results = build_pretrained_anomaly_results(
        primary,
        validation_matrix,
        validation_context,
        model_manifest=model_manifest,
    )
    if len(anomaly_results) != len(validation_context):
        raise RuntimeError("A inferência alterou a quantidade de registros.")

    # Usa apenas medidas financeiras selecionadas durante o treinamento.
    financial_columns = materiality.get_financial_features(feature_manifest)
    materiality.validate_inputs(
        validation_context,
        anomaly_results,
        financial_columns,
    )
    profiled = materiality.build_materiality_dataset(
        validation_context,
        anomaly_results,
        financial_columns,
    )
    if len(profiled) != len(validation_context):
        raise RuntimeError("A análise de materialidade alterou os registros.")

    # A seleção dos candidatos utiliza o P95 calibrado NO TREINO.
    candidates = build_pretrained_alert_candidates(profiled)
    if len(candidates) != int(base["reference_flags"]):
        raise RuntimeError("Candidatos não correspondem aos sinais p95 de treino.")
    validate_record_identity(candidates, allow_empty=True)

    # Cinco modelos por empresa/moeda: todos ajustados com os mesmos registros
    # de treinamento e a mesma lista de features, nunca com o holdout.
    models = {PRIMARY_SEED: primary}
    for seed in RANDOM_STATES:
        if seed == PRIMARY_SEED:
            continue
        models[seed] = train_grouped_models(
            training_matrix=train_matrix,
            training_context=train_context,
            min_group_size=20,
            random_state=seed,
        )
        if models[seed].feature_columns != primary.feature_columns:
            raise RuntimeError("Contrato de features inconsistente entre seeds.")
        if set(models[seed].models) != set(primary.models):
            raise RuntimeError("Grupos inconsistentes entre as seeds.")
        if models[seed].training_fingerprint != primary.training_fingerprint:
            raise RuntimeError("Treinamentos não utilizam a mesma base.")

    stability = build_pretrained_stability(
        models, validation_matrix, validation_context
    )
    if len(stability) != len(validation_context):
        raise RuntimeError("A estabilidade alterou a quantidade de registros.")

    enriched = attach_pretrained_stability(candidates, stability)
    if len(enriched) != len(candidates):
        raise RuntimeError("Candidatos perderam alinhamento com a estabilidade.")
    validate_record_identity(enriched, allow_empty=True)

    classified = None
    if protected_rules is not None:
        classified = build_pretrained_classification(enriched, protected_rules)
        if len(classified) != len(enriched):
            raise RuntimeError("A classificação alterou a quantidade de candidatos.")

    return {
        "training_rows": len(train_context),
        "validation_rows": len(validation_context),
        "model_features": len(train_matrix.columns),
        "group_count": len(primary.models),
        "seeds": len(RANDOM_STATES),
        "primary_flags": int(base["reference_flags"]),
        "automatic_flags": int(base["automatic_flags"]),
        "candidate_count": len(enriched),
        "stable_all_seeds": int(enriched["flag_count"].eq(len(RANDOM_STATES)).sum()),
        "materiality": profiled,
        "candidates": enriched,
        "classified": classified,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Prévia KE24: anomalias, materialidade e estabilidade."
    )
    parser.add_argument(
        "--classify",
        action="store_true",
        help="Aplica regras financeiras privadas previamente configuradas.",
    )
    args = parser.parse_args()

    print("\nKE24 - Drive processed -> Prévia de candidatos a alertas\n")
    print("Somente leitura: nenhuma publicação, nem gravação de modelos/dados.")
    policy, schema, _ = load_privacy_runtime()
    service = build(
        "drive", "v3", credentials=authenticate(), cache_discovery=False
    )
    processed = load_verified_processed_dataframe(service, policy, schema)
    dataset, feature_manifest, _ = build_features_in_memory(processed, policy)

    # O usuário opta explicitamente pela classificação, caso as regras existam.
    rules = load_protected_alert_rules() if args.classify else None
    result = build_alert_preview_in_memory(
        dataset, feature_manifest, policy, protected_rules=rules
    )

    print("\nMaterialidade e estabilidade concluídas em memória!")
    print(f"Registros de treinamento: {result['training_rows']}")
    print(f"Registros de validação: {result['validation_rows']}")
    print(f"Features de treinamento: {result['model_features']}")
    print(f"Grupos treinados: {result['group_count']}")
    print(f"Seeds analisadas: {result['seeds']}")
    print(f"Sinais p95 da seed principal: {result['primary_flags']}")
    print(f"Sinais automáticos: {result['automatic_flags']}")
    print(f"Candidatos para análise: {result['candidate_count']}")
    print(f"Candidatos sinalizados por todas as seeds: {result['stable_all_seeds']}")

    if result["classified"] is None:
        print("Classificação financeira: PENDENTE de regras privadas autorizadas.")
    else:
        # Apenas agregados, sem imprimir valores financeiros ou identificadores.
        status_counts = result["classified"]["classification_status"].value_counts()
        print(f"Candidatos classificados: {len(result['classified'])}")
        print(f"Classificações pendentes: {sum(int(n) for name, n in status_counts.items() if 'PENDING' in str(name).upper())}")
        print("A revisão de negócio ainda é necessária antes de publicar alertas.")

    print("\nStatus: APROVADO (prévia, sem publicação externa).")
    print("Sinais do modelo não comprovam erro financeiro, precisão ou recall.")


if __name__ == "__main__":
    main()
