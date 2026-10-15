import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.privacy.privacy_runtime import load_privacy_runtime

PROJECT_ROOT = Path(__file__).resolve().parents[2]

PREDICTIONS_FILE = (
    PROJECT_ROOT / "data" / "curated" / "ke24_anomaly_results.parquet"
)

REVIEWS_FILE = (
    PROJECT_ROOT / "data" / "curated" / "ke24_evaluation_labels.csv"
)

OUTPUT_FILE = (
    PROJECT_ROOT / "data" / "curated" / "ke24_detection_metrics.json"
)

IDENTITY_COLUMNS = [
    "_source_file",
    "_source_row_number",
]

REVIEW_COLUMNS = [
    "review_status",
    "business_validation",
    "validation_reason",
    "validated_by",
    "validated_at",
]

LABEL_MAPPING = {
    "TRUE_ANOMALY": 1,
    "EXPECTED_BEHAVIOR": 0,
}

ALLOWED_LABELS = set(LABEL_MAPPING) | {
    "NEEDS_INVESTIGATION"
}

def validate_columns(df, required, name):
    missing = [
        column for column in required
        if column not in df.columns
    ]

    if missing:
        raise ValueError(
            f"Colunas ausentes em {name}: "
            + ", ".join(missing)
        )

def validate_identity(df, name):
    validate_columns(df, IDENTITY_COLUMNS, name)

    if df[IDENTITY_COLUMNS].isna().any().any():
        raise ValueError(
            f"Identificadores ausentes em {name}."
        )

    if df.duplicated(subset=IDENTITY_COLUMNS).any():
        raise ValueError(
            f"Registros duplicados em {name}."
        )

    # Não aceitar arquivos de origem não anonimizados
    protected = (
        df["_source_file"]
        .astype("string")
        .str.fullmatch(r"TKN_[0-9a-f]{32}")
        .fillna(False)
    )

    if not protected.all():
        raise ValueError(
            f"Identificadores não protegidos em {name}."
        )

def prepare_evaluation_data(predictions, reviews):
    if predictions.empty:
        raise ValueError(
            "O conjunto de predições está vazio."
        )

    validate_identity(predictions, "predições")
    validate_identity(reviews, "revisões")

    validate_columns(
        predictions,
        ["anomaly_score", "anomaly_flag"],
        "predições",
    )

    validate_columns(reviews, REVIEW_COLUMNS, "revisões")

    scores = pd.to_numeric(
        predictions["anomaly_score"],
        errors="coerce",
    )

    if not np.isfinite(scores.to_numpy(dtype=float)).all():
        raise ValueError("Existem scores inválidos.")

    if not predictions["anomaly_flag"].map(
        lambda value: isinstance(
            value, (bool, np.bool_)
        )
    ).all():
        raise ValueError("anomaly_flag deve ser booleano.")

    # Uma revisão precisa corresponder a uma predição.
    unmatched = reviews[IDENTITY_COLUMNS].merge(
        predictions[IDENTITY_COLUMNS],
        on=IDENTITY_COLUMNS,
        how="left",
        indicator=True,
    )

    if unmatched["_merge"].eq("left_only").any():
        raise ValueError(
            "Existem revisões sem predição correspondente."
        )

    result = predictions.copy()
    result["anomaly_score"] = scores

    result = result.merge(
        reviews[IDENTITY_COLUMNS + REVIEW_COLUMNS],
        on=IDENTITY_COLUMNS,
        how="left",
        validate="one_to_one",
    )

    status = (
        result["review_status"]
        .astype("string")
        .str.strip()
        .str.lower()
        .fillna("pending")
    )

    labels = (
        result["business_validation"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    if not status.isin({"pending", "validated"}).all():
        raise ValueError("review_status inválido.")

    validated = status.eq("validated")
    pending = status.eq("pending")

    if (validated & ~labels.isin(ALLOWED_LABELS)).any():
        raise ValueError(
            "Registros validados possuem classificação inválida."
        )

    if (pending & labels.notna() & labels.ne("")).any():
        raise ValueError(
            "Registros pendentes não podem possuir avaliação."
        )

    # Valida a rastreabilidade das revisões concluídas
    for column in ["validated_by", "validation_reason"]:
        values = (
            result[column]
            .astype("string")
            .str.strip()
        )

        if (validated & (values.isna() | values.eq(""))).any():
            raise ValueError(
                f"Registros validados sem {column}."
            )

    dates = pd.to_datetime(
        result.loc[validated, "validated_at"],
        errors="coerce",
        utc=True,
    )

    if dates.isna().any():
        raise ValueError(
            "Registros validados sem data válida."
        )

    result["review_status"] = status
    result["business_validation"] = labels

    # Somente anomalias confirmadas e comportamentos esperados possuem referência binária.
    result["ground_truth"] = (
        labels.map(LABEL_MAPPING).astype("Int64")
    )

    return result

def calculate_metrics(dataset, prediction_column="anomaly_flag"):
    if dataset.empty:
        raise ValueError("Dataset de avaliação vazio.")

    validate_columns(
        dataset,
        ["ground_truth", prediction_column],
        "avaliação",
    )

    predicted = dataset[prediction_column]

    if not predicted.map(
        lambda value: isinstance(
            value, (bool, np.bool_)
        )
    ).all():
        raise ValueError(
            "As predições devem ser booleanas."
        )

    eligible = dataset.loc[
        dataset["ground_truth"].notna()
    ].copy()

    y_true = (
        eligible["ground_truth"]
        .astype(int)
    )

    y_pred = (
        eligible[prediction_column]
        .astype(bool)
    )

    tp = int(((y_true == 1) & y_pred).sum())
    fp = int(((y_true == 0) & y_pred).sum())
    tn = int(((y_true == 0) & ~y_pred).sum())
    fn = int(((y_true == 1) & ~y_pred).sum())

    precision = (
        tp / (tp + fp)
        if tp + fp > 0 else None
    )

    recall = (
        tp / (tp + fn)
        if tp + fn > 0 else None
    )

    f1_score = (
        2 * precision * recall / (precision + recall)
        if (
            precision is not None
            and recall is not None
            and precision + recall > 0
        )
        else None
    )

    total = len(dataset)
    labeled = len(eligible)

    if labeled == 0:
        coverage_status = "NO_RESOLVED_REVIEWS"
    elif labeled < total:
        coverage_status = "PARTIAL_COVERAGE"
    else:
        coverage_status = "FULL_COVERAGE"

    return {
        "total_records": total,
        "labeled_records": labeled,
        "coverage_percentage": round(
            labeled / total * 100, 2
        ),
        "coverage_status": coverage_status,
        "review_scope": "REVIEWED_RECORDS_ONLY",
        "needs_investigation": int(
            dataset["business_validation"]
            .eq("NEEDS_INVESTIGATION")
            .fillna(False)
            .sum()
        ) if "business_validation" in dataset else 0,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1_score": f1_score,
    }

def main():
    # Operação real continua sujeita à aprovação corporativa
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

    metrics = calculate_metrics(dataset)

    OUTPUT_FILE.parent.mkdir(
        parents=True, exist_ok=True
    )

    with OUTPUT_FILE.open(
        "w", encoding="utf-8"
    ) as file:
        json.dump(
            metrics,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print("KE24 - Métricas de detecção")
    print(json.dumps(metrics, indent=2))
    print(f"Resultado: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
