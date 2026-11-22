# KE24_PROCESSED_SINGLE_PASS_V1: verificacao e uso dos mesmos bytes Parquet.
# KE24_DRIVE_READ_RETRY_V1: leituras somente leitura com retry limitado.
from src.pipeline.ke24_drive_read_retry import execute_drive_read
import hashlib
import json
from io import BytesIO

import pandas as pd
from googleapiclient.discovery import build

from src.features import build_ke24_ml_features as features
from src.ingestion.google_drive_client import authenticate
from src.loading.ke24_drive_manifest import (
    MANIFEST_NAME,
    list_processed_files,
    validate_manifest_document,
)
from src.loading.plan_ke24_incremental import (
    FILE_ID,
    MANIFEST_COLUMNS,
    build_file_manifest,
)
from src.ml.prepare_ke24_training_matrix import split_temporal_by_group
from src.privacy.privacy_runtime import load_privacy_runtime
from src.privacy.validate_protected_dataset import validate_protected_dataset


def _manifest_signature(manifest):
    #Compara identidade, período e quantidade sem imprimir valores protegidos
    if manifest.columns.duplicated().any() or not set(MANIFEST_COLUMNS).issubset(manifest.columns):
        raise ValueError("Colunas do manifesto incompatíveis.")

    return sorted(
        (
            str(record[FILE_ID]),
            str(record["company_code"]),
            int(record["period"]),
            str(record["period_year"]),
            str(record["currency"]),
            int(record["record_count"]),
        )
        for _, record in manifest.iterrows()
    )


def load_verified_processed_dataframe(service, privacy_policy, privacy_schema):
    
    # Verifica cada Parquet uma unica vez e usa esses MESMOS bytes para leitura.
    files = list_processed_files(service)
    if not files:
        raise RuntimeError("Nenhum lote processed publicado no Google Drive.")
    manifest_files = [item for item in files if item["name"] == MANIFEST_NAME]
    if len(manifest_files) != 1:
        raise RuntimeError("Manifesto remoto ausente ou duplicado.")

    raw_manifest = execute_drive_read(lambda: service.files().get_media(
        fileId=manifest_files[0]["id"]
    ))
    document = json.loads(raw_manifest.decode("utf-8"))
    documented = validate_manifest_document(document)
    expected_ids = {manifest_files[0]["id"]} | {
        batch["parquet_id"] for batch in document["batches"]
    }
    remote = {item["id"]: item for item in files}
    if set(remote) != expected_ids or len(remote) != len(files):
        raise RuntimeError("Arquivos do processed divergentes do manifesto.")

    processed_frames = []
    for batch in document["batches"]:
        parquet_id = batch["parquet_id"]
        if remote[parquet_id]["name"] != batch["parquet_name"]:
            raise RuntimeError("Nome remoto de Parquet inconsistente.")

        payload = execute_drive_read(
            lambda: service.files().get_media(fileId=parquet_id)
        )
        if (
            len(payload) != batch["byte_count"]
            or hashlib.sha256(payload).hexdigest() != batch["parquet_sha256"]
        ):
            raise RuntimeError("Falha de integridade do Parquet publicado.")

        with BytesIO(payload) as buffer:
            frame = pd.read_parquet(buffer, engine="pyarrow")

        validate_protected_dataset(
            frame, privacy_policy, require_all_columns=True
        )
        expected_source_ids = set(batch["source_file_ids"])
        if (
            len(frame) != batch["record_count"]
            or set(frame[FILE_ID].astype(str)) != expected_source_ids
        ):
            raise RuntimeError("Quantidade ou origem divergente no lote processed.")
        processed_frames.append(frame)

    processed = pd.concat(processed_frames, ignore_index=True)
    expected_columns = (
        set(privacy_schema["columns"])
        | set(privacy_policy["metadata_actions"])
        | {"calendar_year", "period_key", "period_date"}
    )
    if processed.columns.duplicated().any() or set(processed.columns) != expected_columns:
        raise RuntimeError("Layout da camada processed divergente do contrato.")

    validate_protected_dataset(
        processed, privacy_policy, require_all_columns=True
    )
    actual_manifest = build_file_manifest(processed)
    if _manifest_signature(actual_manifest) != _manifest_signature(documented):
        raise RuntimeError("Registros e manifesto não correspondem.")

    # Bloqueia alteracoes no manifesto ou na composicao da pasta durante a leitura.
    # O reread e apenas do pequeno manifesto, nunca dos Parquets ja validados.
    final_manifest = execute_drive_read(
        lambda: service.files().get_media(fileId=manifest_files[0]["id"])
    )
    if final_manifest != raw_manifest:
        raise RuntimeError("Manifesto foi alterado durante a leitura; execução bloqueada.")

    final_files = list_processed_files(service)
    if sorted((item["id"], item["name"]) for item in final_files) != sorted(
        (item["id"], item["name"]) for item in files
    ):
        raise RuntimeError("Pasta processed alterada durante a leitura; execução bloqueada.")

    return processed


def build_features_in_memory(processed, privacy_policy):
    validate_protected_dataset(
        processed, privacy_policy, require_all_columns=True
    )

    missing_context = set(features.CONTEXT_COLUMNS) - set(processed.columns)
    if missing_context:
        raise ValueError("Colunas de contexto ausentes no processed.")

    # Não eliminar o holdout temporal: a escolha de medidas deve usar só treino.
    train, validation = split_temporal_by_group(processed)
    measure_columns = features.get_measure_columns(processed)
    train_measures = features.prepare_measure_matrix(train, measure_columns)
    active, inactive = features.select_active_measures(train_measures)
    measures = features.prepare_measure_matrix(processed, measure_columns)
    engineered = features.build_engineered_features(measures)

    dataset = pd.concat(
        [
            processed[features.CONTEXT_COLUMNS].reset_index(drop=True),
            measures[active].reset_index(drop=True),
            engineered.reset_index(drop=True),
        ],
        axis=1,
    )
    features.validate_output(processed, dataset, list(engineered.columns))
    feature_manifest = features.build_feature_manifest(
        measure_columns, active, list(engineered.columns)
    )
    if len(dataset) != len(processed):
        raise RuntimeError("Perda de linhas na construção das features.")

    stats = {
        "train_rows": len(train),
        "validation_rows": len(validation),
        "active_measures": len(active),
        "inactive_measures": len(inactive),
        "engineered_features": len(engineered.columns),
    }
    return dataset, feature_manifest, stats


def main():
    print("\nKE24 - Drive processed -> Feature Engineering (somente leitura)\n")
    policy, schema, _ = load_privacy_runtime()
    service = build(
        "drive", "v3", credentials=authenticate(), cache_discovery=False
    )
    processed = load_verified_processed_dataframe(service, policy, schema)
    print("Processed remoto verificado.")
    print(f"Registros processed: {len(processed)}")
    print(f"Colunas processed: {len(processed.columns)}")

    dataset, feature_manifest, stats = build_features_in_memory(processed, policy)
    print("\nFeature Engineering concluído em memória!")
    print(f"Registros de treino: {stats['train_rows']}")
    print(f"Registros de validação: {stats['validation_rows']}")
    print(f"Medidas selecionadas: {stats['active_measures']}")
    print(f"Medidas inativas: {stats['inactive_measures']}")
    print(f"Features derivadas: {stats['engineered_features']}")
    print(f"Colunas de ML: {len(dataset.columns)}")
    print(f"Entradas no manifesto de features: {len(feature_manifest)}")
    print("\nStatus: APROVADO. Nenhum dado financeiro gravado localmente ou no Drive.")


if __name__ == "__main__":
    main()
