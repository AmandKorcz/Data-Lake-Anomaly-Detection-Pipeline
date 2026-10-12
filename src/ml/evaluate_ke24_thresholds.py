import json

import numpy as np
import pandas as pd

from src.ml.evaluate_ke24_business_metrics import (
    PREDICTIONS_FILE,
    REVIEWS_FILE,
    PROJECT_ROOT,
    prepare_evaluation_data,
    calculate_metrics,
)
from src.privacy.privacy_runtime import load_privacy_runtime

OUTPUT_FILE = (
    PROJECT_ROOT / "data" / "curated" / "ke24_threshold_comparison.json"
)

DEFAULT_THRESHOLDS = (80, 90, 95, 99)

def compare_thresholds(dataset, thresholds=DEFAULT_THRESHOLDS):
    if dataset.empty:
        raise ValueError("Dataset de avaliação vazio.")

    required = {"anomaly_score", "ground_truth"}

    if not required.issubset(dataset.columns):
        raise ValueError(
            "Campos necessários para comparação ausentes."
        )

    scores = pd.to_numeric(
        dataset["anomaly_score"],
        errors="coerce",
    )

    if not np.isfinite(scores.to_numpy(dtype=float)).all():
        raise ValueError("Existem scores inválidos.")

    if not thresholds:
        raise ValueError("Nenhum threshold informado.")

    if any(
        not isinstance(value, (int, float))
        or not np.isfinite(value)
        or not 0 < value <= 100
        for value in thresholds
    ):
        raise ValueError(
            "Os thresholds devem estar entre 0 e 100."
        )

    if len(set(thresholds)) != len(thresholds):
        raise ValueError("Thresholds duplicados.")

    # Calcula os percentis no universo inteiro, antes de filtrar os registros revisados.
    percentiles = (
        scores.rank(method="average", pct=True) * 100
    )

    comparisons = []

    for threshold in thresholds:
        result = dataset.copy()

        result["predicted_anomaly"] = (
            percentiles >= threshold
        )

        metrics = calculate_metrics(
            result,
            prediction_column="predicted_anomaly",
        )

        comparisons.append({
            "threshold_percentile": threshold,
            "selected_total": int(
                result["predicted_anomaly"].sum()
            ),
            **metrics,
        })

    return comparisons

def main():
    # Bloqueia processamento operacional sem aprovação
    load_privacy_runtime()

    if not PREDICTIONS_FILE.is_file():
        raise FileNotFoundError(
            "Resultados de anomalias não encontrados."
        )

    if not REVIEWS_FILE.is_file():
        raise FileNotFoundError(
            "Avaliações humanas não encontradas."
        )

    predictions = pd.read_parquet(PREDICTIONS_FILE)
    reviews = pd.read_csv(
        REVIEWS_FILE,
        encoding="utf-8-sig",
    )

    dataset = prepare_evaluation_data(
        predictions, reviews
    )

    comparison = compare_thresholds(dataset)

    OUTPUT_FILE.parent.mkdir(
        parents=True, exist_ok=True
    )

    with OUTPUT_FILE.open(
        "w", encoding="utf-8"
    ) as file:
        json.dump(
            comparison,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print("KE24 - Comparação de Thresholds")
    print(json.dumps(comparison, indent=2))
    print(f"Resultado: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()