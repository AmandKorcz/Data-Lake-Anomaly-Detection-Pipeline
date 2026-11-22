from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

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
FULL_MODEL_DIR = Path.home() / "AppData" / "Local" / "KE24" / "models" / "full"


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


def prepare_full_training_matrix(dataset, temporal_result):
    selected = list(temporal_result["train_matrix"].columns)
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("Contrato de features temporais invalido.")

    expected = (
        len(temporal_result["train_context"])
        + len(temporal_result["validation_context"])
    )
    if not expected or len(dataset) != expected:
        raise RuntimeError(
            "Conjunto completo difere da divisao temporal validada."
        )

    full_matrix = training.build_training_matrix(dataset, selected).reset_index(drop=True)
    if len(full_matrix) != expected or list(full_matrix.columns) != selected:
        raise RuntimeError("Matriz completa difere do contrato temporal.")
    return full_matrix


def train_full_model_in_memory(dataset, temporal_result):
    full_matrix = prepare_full_training_matrix(dataset, temporal_result)
    bundle = train_grouped_models(
        training_matrix=full_matrix,
        training_context=dataset.reset_index(drop=True),
        min_group_size=20,
        random_state=PRIMARY_SEED,
    )

    if (
        bundle.feature_columns != tuple(full_matrix.columns)
        or set(bundle.models) != set(temporal_result["model"].models)
        or sum(bundle.group_sizes.values()) != len(full_matrix)
    ):
        raise RuntimeError("Retreinamento completo inconsistente com a validacao temporal.")

    return bundle, len(full_matrix)


def build_full_model_version(fingerprint: str, now=None):
    import re
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("Identificador de treinamento invalido.")
    instant = datetime.now(timezone.utc) if now is None else now
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Data de versao precisa conter fuso horario.")
    instant = instant.astimezone(timezone.utc)
    return f"ke24_full_{instant.strftime('%Y%m%dT%H%M%S%fZ')}_{fingerprint[:10]}"


def save_full_model(bundle, *, root=None):
    from src.ml.ke24_model_store import load_model_bundle, save_model_bundle

    target_root = FULL_MODEL_DIR if root is None else Path(root)
    version = build_full_model_version(bundle.training_fingerprint)
    destination = save_model_bundle(bundle, target_root, version)
    reloaded, manifest = load_model_bundle(target_root, version)
    if (
        reloaded.training_fingerprint != bundle.training_fingerprint
        or reloaded.feature_columns != bundle.feature_columns
        or reloaded.group_sizes != bundle.group_sizes
        or manifest.get("model_version") != version
    ):
        raise RuntimeError("Verificacao do modelo versionado reprovada.")
    return destination, version


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="KE24: valida treino temporal e opcionalmente retreina modelo final"
    )
    parser.add_argument(
        "--full-retrain", action="store_true",
        help="Valida temporalmente e retreina modelo em memoria com todo o Processed",
    )
    parser.add_argument(
        "--save-model", action="store_true",
        help="Salva versao local do modelo completo (exige --full-retrain)",
    )
    args = parser.parse_args(argv)
    if args.save_model and not args.full_retrain:
        parser.error("--save-model exige --full-retrain")

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
    if not args.full_retrain:
        print("\nStatus: APROVADO. Nenhum dado financeiro gravado no Drive ou em disco.")
        print("Observação: sinalização de anomalias não é medida de precisão/recall.")
        return 0

    print("Status da validacao temporal: APROVADO.")

    print("\n--------------------------------------------------------------------")
    print("KE24 | RETREINAMENTO COMPLETO")
    print("--------------------------------------------------------------------")
    bundle, row_count = train_full_model_in_memory(features_dataset, result)
    print(f"Registros utilizados: {row_count}")
    print(f"Features utilizadas: {len(bundle.feature_columns)}")
    print(f"Grupos empresa/moeda: {len(bundle.models)}")
    print("Validacao temporal preservada; nenhuma metrica de precisao inferida.")

    if args.save_model:
        destination, version = save_full_model(bundle)
        print(f"Versao salva e verificada: {version}")
        print(f"Armazenamento privado local: {destination}")
        print("Este modelo AINDA NAO foi ativado para gerar alertas.")
        print("Historico, Google Sheets e Looker Studio: INALTERADOS.")
    else:
        print("Previa concluida: modelo final apenas em memoria.")
        print("Para salvar, execute novamente com --full-retrain --save-model.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
