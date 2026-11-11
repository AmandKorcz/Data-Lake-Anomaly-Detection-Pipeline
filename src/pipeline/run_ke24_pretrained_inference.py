from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.privacy.validate_protected_dataset import (
    validate_protected_dataset,
)

from src.ml import prepare_ke24_training_matrix as training
from src.ml import profile_ke24_anomaly_materiality as materiality

from src.ml.ke24_model_store import load_model_bundle

from src.ml.evaluate_ke24_isolation_forest_stability import (
    RANDOM_STATES,
)

from src.ml.build_ke24_pretrained_anomaly_results import (
    build_pretrained_anomaly_results,
)

from src.ml.build_ke24_pretrained_alert_candidates import (
    build_pretrained_alert_candidates,
    attach_pretrained_stability,
)

from src.ml.build_ke24_pretrained_stability import (
    build_pretrained_stability,
)

from src.ml.classify_ke24_pretrained_alerts import (
    build_pretrained_classification,
)

from src.alerts.build_ke24_pretrained_outputs import (
    build_pretrained_outputs,
)

from src.features.build_ke24_pretrained_inference_features import (
    build_pretrained_inference_features,
)

from src.alerts.ke24_pretrained_csv_transaction import (
    locked_pretrained_csv_pair,
    _publish_pretrained_csv_pair_locked,
)

def load_stability_model_versions(model_root, versions):
    if (
        not isinstance(versions, dict)
        or set(versions) != set(RANDOM_STATES)
    ):
        raise ValueError("Missing stability model versions")

    models = {}
    manifests = {}

    for seed in RANDOM_STATES:
        bundle, manifest = load_model_bundle(
            model_root,
            versions[seed],
        )

        # Prevent assigning a saved model to the wrong seed.
        if any(
            model.random_state != seed
            for model in bundle.models.values()
        ):
            raise ValueError("Incorrect stability model seed")

        models[seed] = bundle
        manifests[seed] = manifest

    primary = models[42]

    # All five models must use the same training dataset.
    fingerprints = {
        manifest.get("training_fingerprint")
        for manifest in manifests.values()
    }

    if len(fingerprints) != 1:
        raise ValueError(
            "Stability models use different training datasets"
        )

    # Validate feature and group compatibility.
    for seed, bundle in models.items():
        if (
            bundle.feature_columns != primary.feature_columns
            or set(bundle.models) != set(primary.models)
            or bundle.group_sizes != primary.group_sizes
        ):
            raise ValueError(
                "Incompatible stability model contracts"
            )

    return models, manifests[42]


def execute_pretrained_pipeline(
    new_features,
    feature_manifest,
    model_root,
    versions,
    protected_rules,
    privacy_policy,
    previous_history=None,
    detected_at_utc=None,
):
    if detected_at_utc is None:
        detected_at_utc = datetime.now(timezone.utc)

    # Input must already be protected.
    validate_protected_dataset(
        new_features,
        privacy_policy,
        require_all_columns=False,
    )

    models, model_manifest = (
        load_stability_model_versions(
            model_root,
            versions,
        )
    )

    primary = models[42]

    if not isinstance(feature_manifest, pd.DataFrame):
        raise ValueError("Invalid feature manifest")

    selected = training.get_selected_features(
        feature_manifest
    )

    if not set(primary.feature_columns).issubset(
        set(selected)
    ):
        raise ValueError(
            "Model features differ from training manifest"
        )

    # Use the exact feature order stored at training.
    matrix = training.build_training_matrix(
        new_features,
        list(primary.feature_columns),
    )

    anomaly_results = (
        build_pretrained_anomaly_results(
            primary,
            matrix,
            new_features,
            model_manifest=model_manifest,
        )
    )

    financial = materiality.get_financial_features(
        feature_manifest
    )

    materiality.validate_inputs(
        new_features,
        anomaly_results,
        financial,
    )

    profiled = materiality.build_materiality_dataset(
        new_features,
        anomaly_results,
        financial,
    )

    candidates = build_pretrained_alert_candidates(
        profiled
    )

    stability = build_pretrained_stability(
        models,
        matrix,
        new_features,
    )

    enriched_candidates = attach_pretrained_stability(
        candidates,
        stability,
    )

    classified = build_pretrained_classification(
        enriched_candidates,
        protected_rules,
    )

    history, dashboard = build_pretrained_outputs(
        classified,
        previous_history,
        detected_at_utc,
        privacy_policy,
    )

    return {
        "scored_count": len(new_features),
        "candidate_count": len(classified),
        "model_version": model_manifest["model_version"],
        "classified": classified,
        "history": history,
        "dashboard": dashboard,
    }

def execute_pretrained_pipeline_from_processed(
    processed,
    feature_manifest,
    model_root,
    versions,
    protected_rules,
    privacy_policy,
    previous_history=None,
    detected_at_utc=None,
):
    prepared_features = (
        build_pretrained_inference_features(
            processed,
            feature_manifest,
            privacy_policy,
        )
    )

    return execute_pretrained_pipeline(
        prepared_features,
        feature_manifest,
        model_root,
        versions,
        protected_rules,
        privacy_policy,
        previous_history=previous_history,
        detected_at_utc=detected_at_utc,
    )

def execute_pretrained_pipeline_to_files(
    new_features,
    feature_manifest,
    model_root,
    versions,
    protected_rules,
    privacy_policy,
    history_file,
    dashboard_file,
):
    history_path = Path(history_file)
    dashboard_path = Path(dashboard_file)

    if history_path.resolve() == dashboard_path.resolve():
        raise ValueError(
            "History and dashboard paths must differ"
        )

    # Lock both outputs and recover any pending transaction.
    with locked_pretrained_csv_pair(
        history_path,
        dashboard_path,
    ):
        if history_path.exists():
            previous = pd.read_csv(
                history_path,
                encoding="utf-8-sig",
            )
        else:
            previous = None

        result = execute_pretrained_pipeline(
            new_features,
            feature_manifest,
            model_root,
            versions,
            protected_rules,
            privacy_policy,
            previous_history=previous,
        )

        _publish_pretrained_csv_pair_locked(
            result["history"],
            result["dashboard"],
            history_path,
            dashboard_path,
        )

    return result


def main_temporal_preflight():
    from src.features import build_ke24_ml_features as features

    root = Path(__file__).resolve().parents[2]
    curated = root / "data" / "curated"

    # Modelos treinados
    model_root = root / "data" / "models" / "ke24_temporal"

    versions = {
        seed: f"ke24_real_temporal_s{seed}"
        for seed in RANDOM_STATES
    }

    models, _ = load_stability_model_versions(
        model_root,
        versions,
    )

    # Dados reservados para validação
    validation = pd.read_parquet(
        curated / "ke24_validation_features.parquet"
    )

    # Manifesto definido apenas no treinamento
    manifest = pd.read_csv(
        curated / "ke24_ml_feature_manifest.csv",
        encoding="utf-8-sig",
    )

    model_features = list(models[42].feature_columns)

    if not set(model_features).issubset(
        set(training.get_selected_features(manifest))
    ):
        raise ValueError("Contrato de features incompatível.")

    matrix = training.build_training_matrix(
        validation,
        model_features,
    )

    # Verifica medidas novas no período de validação
    processed = pd.read_parquet(
        root / "data" / "processed" / "ke24_processed.parquet"
    )

    _, validation_full = training.split_temporal_by_group(
        processed
    )

    if len(validation_full) != len(validation):
        raise ValueError("Conjunto de validação inconsistente.")

    inactive = manifest.loc[
        manifest["feature_type"].eq("financial_measure")
        & ~manifest["selected_for_model"]
        .astype(str).str.lower().eq("true"),
        "feature",
    ].tolist()

    newly_active = [
        column
        for column in inactive
        if validation_full[column].ne(0).any()
    ]

    print("\nDIAGNOSTICO DA INFERENCIA TEMPORAL")
    print("Versoes carregadas:", len(models))
    print("Grupos por versao:", len(models[42].models))
    print("Features do modelo:", len(matrix.columns))
    print("Registros reservados:", len(validation))
    print("Medidas novas ativas:", len(newly_active))
    print("\nDiagnostico previo concluido.")

    return models, matrix, validation, newly_active, validation_full


def main_temporal_scoring():
    from src.ml.ke24_grouped_model_runtime import (
        score_new_records,
    )

    print("\nKE24 - Inferencia Temporal\n")

    # Reutiliza os dados e modelos validados
    models, matrix, validation, newly_active, validation_full = (
        main_temporal_preflight()
    )

    # Modelo principal: seed 42
    primary_results = score_new_records(
        models[42],
        matrix,
        validation,
    )

    # Compara os resultados das cinco sementes
    stability = build_pretrained_stability(
        models,
        matrix,
        validation,
    )

    if (
        len(primary_results) != len(validation)
        or len(stability) != len(validation)
    ):
        raise ValueError(
            "Quantidade de resultados inconsistente."
        )

    # Identifica os registros sinalizados
    primary_alerts = int(
        primary_results["reference_p95_flag"].sum()
    )

    any_seed = int(
        stability["flag_count"].gt(0).sum()
    )

    all_seeds = int(
        stability["flag_count"].eq(5).sum()
    )

    
    # Confere o alinhamento entre as duas fontes
    identity = [
        "_source_file_sha256",
        "_source_row_number",
        "company_code",
    ]

    expected = (
        validation[identity]
        .reset_index(drop=True)
        .astype("string")
    )

    actual = (
        validation_full[identity]
        .reset_index(drop=True)
        .astype("string")
    )

    if not expected.equals(actual):
        raise ValueError(
            "Registros de validacao fora de ordem."
        )

    # Identifica quais registros possuem movimento novo
    drift_mask = (
        validation_full[newly_active]
        .fillna(0)
        .ne(0)
        .any(axis=1)
        .reset_index(drop=True)
        if newly_active
        else pd.Series(False, index=range(len(validation)))
    )

    primary_flags = (
        primary_results["reference_p95_flag"]
        .astype(bool)
        .reset_index(drop=True)
    )

    stable_flags = (
        stability["flag_count"]
        .eq(len(models))
        .reset_index(drop=True)
    )

    
    # Prepara os sinais tecnicos da seed principal
    selected = primary_flags

    signals = validation.loc[
        selected,
        [
            "_source_file_sha256",
            "_source_row_number",
            "company_code",
            "currency",
            "period_key",
        ],
    ].reset_index(drop=True).copy()

    signals["anomaly_score"] = (
        primary_results.loc[selected, "anomaly_score"]
        .to_numpy()
    )

    signals["training_p95_threshold"] = (
        primary_results.loc[
            selected, "training_p95_threshold"
        ].to_numpy()
    )

    signals["stability_rate"] = (
        stability.loc[selected, "stability_rate"]
        .to_numpy()
    )

    signals["new_measure_drift"] = (
        drift_mask.loc[selected].to_numpy(dtype=bool)
    )

    signals["analysis_status"] = (
        signals["new_measure_drift"].map({
            True: "PENDING_DRIFT_REVIEW",
            False: "PENDING_BUSINESS_CLASSIFICATION",
        })
    )

    # Confere quantidade e identidade dos registros
    if len(signals) != int(primary_flags.sum()):
        raise ValueError("Contagem de sinais inconsistente.")

    identity = [
        "_source_file_sha256",
        "_source_row_number",
        "company_code",
    ]

    if signals.duplicated(subset=identity).any():
        raise ValueError("Sinais com identidade duplicada.")

    # Salva a previa local para o dashboard
    output = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "curated"
        / "ke24_temporal_signals_preview.csv"
    )

    output.parent.mkdir(parents=True, exist_ok=True)

    signals.to_csv(
        output,
        index=False,
        encoding="utf-8-sig",
    )

    print("\nPREVIA DE PUBLICACAO")
    print("Sinais gerados:", len(signals))
    print(
        "Pendentes de revisao de drift:",
        int(signals["new_measure_drift"].sum())
    )
    print(
        "Pendentes de classificacao financeira:",
        int((~signals["new_measure_drift"]).sum())
    )
    print("Arquivo local gerado:", output.name)

    print("\nIMPACTO DO MOVIMENTO FINANCEIRO NOVO")
    print("Registros com movimento novo:", int(drift_mask.sum()))
    print(
        "Sinalizados pela seed 42 com movimento novo:",
        int((drift_mask & primary_flags).sum())
    )
    print(
        "Sinalizados pelas 5 seeds com movimento novo:",
        int((drift_mask & stable_flags).sum())
    )
    print(
        "Sinalizados pela seed 42 sem movimento novo:",
        int((~drift_mask & primary_flags).sum())
    )

    print("\nRESULTADO DA INFERENCIA")
    print("Registros analisados:", len(validation))
    print("Versoes utilizadas:", len(models))
    print("Alertas da seed 42:", primary_alerts)
    print("Sinalizados por pelo menos 1 seed:", any_seed)
    print("Sinalizados pelas 5 seeds:", all_seeds)

    print("\nMONITORAMENTO DE DRIFT")
    print("Medidas com movimento novo:", len(newly_active))

    if newly_active:
        print(
            "ATENCAO: movimento novo detectado. "
            "Revisao necessaria antes da classificacao "
            "e publicacao dos alertas."
        )

    print(
        "\nInferencia concluida. "
        "Nenhuma publicacao externa realizada."
    )



if __name__ == "__main__":
    import sys
    import subprocess

    args = sys.argv[1:]

    if not args or args == ["--check"]:
        main_temporal_preflight()

    elif args == ["--score"]:
        main_temporal_scoring()

    elif args == ["--score-publish"]:
        # Executa a inferencia dos modelos treinados
        main_temporal_scoring()

        print("\nPublicando resultados no Google Sheets...")

        # Publica somente se a inferencia terminou com sucesso
        subprocess.run(
            [
                sys.executable,
                "-m",
                "src.loading.publish_ke24_google_sheets",
                "--publish",
            ],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
        )

        print("\nPipeline e publicacao concluidos!")

    else:
        raise SystemExit(
            "Uso: python -m "
            "src.pipeline.run_ke24_pretrained_inference "
            "[--check | --score | --score-publish]"
        )
