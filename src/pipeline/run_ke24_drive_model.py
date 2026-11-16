import numpy as np
from googleapiclient.discovery import build

from src.ingestion.google_drive_client import authenticate
from src.ml import prepare_ke24_training_matrix as training
from src.ml.ke24_grouped_model_runtime import (
    score_new_records,
    train_grouped_models,
)
from src.pipeline.run_ke24_drive_features import (
    build_features_in_memory,
    load_verified_processed_dataframe,
)
from src.privacy.privacy_runtime import load_privacy_runtime
from src.privacy.validate_protected_dataset import validate_protected_dataset


PRIMARY_SEED = 42


def prepare_temporal_matrices(dataset, feature_manifest, privacy_policy):
    validate_protected_dataset(
        dataset, privacy_policy, require_all_columns=False
    )

    if feature_manifest["feature"].duplicated().any():
        raise ValueError("Manifesto de features possui nomes duplicados.")

    train_context, validation_context = training.split_temporal_by_group(
        dataset
    )
    selected = training.get_selected_features(feature_manifest)

    # O perfil e a escolha das features constantes usam SOMENTE o treino.
    candidate_matrix = training.build_training_matrix(
        train_context, selected
    )
    profile = training.build_feature_profile(candidate_matrix)
    constants = set(
        profile.loc[profile["is_constant"], "feature"].tolist()
    )
    final_features = [name for name in selected if name not in constants]

    if not final_features:
        raise ValueError("Todas as features são constantes no treinamento.")

    train_matrix = candidate_matrix[final_features].reset_index(drop=True)
    validation_matrix = training.build_training_matrix(
        validation_context, final_features
    ).reset_index(drop=True)

    if (
        len(train_matrix) != len(train_context)
        or len(validation_matrix) != len(validation_context)
    ):
        raise RuntimeError("Matriz e contexto perderam alinhamento.")

    return {
        "train_matrix": train_matrix,
        "train_context": train_context,
        "validation_matrix": validation_matrix,
        "validation_context": validation_context,
        "selected_count": len(selected),
        "removed_constants": len(constants),
    }


def run_model_in_memory(dataset, feature_manifest, privacy_policy):
    prepared = prepare_temporal_matrices(
        dataset, feature_manifest, privacy_policy
    )

    bundle = train_grouped_models(
        training_matrix=prepared["train_matrix"],
        training_context=prepared["train_context"],
        min_group_size=20,
        random_state=PRIMARY_SEED,
    )

    scores = score_new_records(
        trained=bundle,
        new_matrix=prepared["validation_matrix"],
        new_context=prepared["validation_context"],
    )

    if (
        len(scores) != len(prepared["validation_context"])
        or scores.isna().any().any()
        or not np.isfinite(scores["anomaly_score"].to_numpy(dtype=float)).all()
    ):
        raise RuntimeError("Resultado da inferência temporal inconsistente.")

    return {
        **prepared,
        "model": bundle,
        "scores": scores,
        "group_count": len(bundle.models),
        "reference_flags": int(scores["reference_p95_flag"].sum()),
        "automatic_flags": int(scores["anomaly_flag_auto"].sum()),
    }


def main():
    print("\nKE24 - Drive processed -> Isolation Forest temporal\n")
    print("Modo somente leitura: nenhum dado financeiro será publicado.")

    policy, schema, _ = load_privacy_runtime()
    service = build(
        "drive", "v3", credentials=authenticate(), cache_discovery=False
    )

    processed = load_verified_processed_dataframe(service, policy, schema)
    features_dataset, feature_manifest, _ = build_features_in_memory(
        processed, policy
    )
    result = run_model_in_memory(features_dataset, feature_manifest, policy)

    print("\nTreinamento e inferência temporal concluídos!")
    print(f"Registros de treino: {len(result['train_context'])}")
    print(f"Registros de validação: {len(result['validation_context'])}")
    print(f"Features inicialmente selecionadas: {result['selected_count']}")
    print(f"Features constantes removidas: {result['removed_constants']}")
    print(f"Features finais do modelo: {len(result['train_matrix'].columns)}")
    print(f"Grupos empresa/moeda treinados: {result['group_count']}")
    print(f"Registros avaliados: {len(result['scores'])}")
    print(f"Sinais acima do p95 de treino: {result['reference_flags']}")
    print(f"Sinais automáticos do modelo: {result['automatic_flags']}")
    print("\nStatus: APROVADO. Nenhum dado financeiro gravado no Drive ou em disco.")
    print("Observação: sinalização de anomalias não é medida de precisão/recall.")


if __name__ == "__main__":
    main()
