from pathlib import Path

import pandas as pd
from sklearn.ensemble import IsolationForest

import json

import numpy as np

from src.privacy.validate_protected_dataset import (
    validate_protected_dataset,
)

#Configurações
PROJECT_ROOT = Path(__file__).resolve().parents[2]

PRIVACY_POLICY_FILE = (
    PROJECT_ROOT
    / "config"
    / "ke24_privacy_policy.json"
)

TRAINING_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_training_matrix.parquet"
)

CONTEXT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_ml_features.parquet"
)

OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_anomaly_results.parquet"
)

OUTPUT_CSV = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_anomaly_results.csv"
)

CONTEXT_COLUMNS = [
    "_load_id",
    "_source_file_sha256",
    "_source_file",
    "_source_row_number",
    "period",
    "period_year",
    "calendar_year",
    "period_key",
    "period_date",
    "company_code",
    "profit_center",
    "division",
    "product",
    "record_type",
    "reference_document"
]

#Validação
def validate_inputs(training_matrix, context_data):

    if training_matrix.empty:
        raise ValueError("A matriz de treinamento está vazia")

    if len(training_matrix) != len(context_data):
        raise ValueError("A qualidade de registros da matriz não correposnde ao dataset de contexto.")

    missing_context = [
        column
        for column in CONTEXT_COLUMNS
        if column not in context_data.columns
    ]

    if missing_context:
        raise ValueError(
            "Colunas de contexto ausentes: "
            +", ".join(missing_context)
        )

    # Valida os identificadores antes de treinar o modelo.
    with PRIVACY_POLICY_FILE.open(
        "r",
        encoding="utf-8"
    ) as file:
        privacy_policy = json.load(file)

    validate_protected_dataset(
        context_data,
        privacy_policy,
        require_all_columns=False,
    )

    if training_matrix.isna().any().any():
        raise ValueError("A matriz de treinamento possui valores nulos.")

#Treinamento
def train_isolation_forest(training_matrix, random_state=42):

    model = IsolationForest(
        n_estimators=300,
        contamination="auto",
        random_state=random_state,
        n_jobs=-1,
    )

    model.fit(training_matrix)

    return model

#Scoring
def build_anomaly_scores(model, training_matrix):

    #scores_samples - valores menores representam observações mais anômalas
    raw_score = model.score_samples(training_matrix)

    #Inverti o sinal pra facilitar a interpretaçaõ, quanto maior, mais anômalo
    anomaly_score = (-raw_score)

    decision_score = (
        model.decision_function(training_matrix)
    )

    prediction = model.predict(training_matrix)

    scores = pd.DataFrame({
        "anomaly_score": anomaly_score,
        "decision_score": decision_score,
        "anomaly_flag": prediction == -1,
    })

    scores["anomaly_rank"] = (
        scores["anomaly_score"]
        .rank(
            method="first",
            ascending=False
        )
        .astype(int)
    )

    return scores


def build_grouped_anomaly_scores(
    training_matrix,
    context_data,
    min_group_size=20,
    random_state=42,
):
    validate_inputs(training_matrix, context_data)

    required = {
        "company_code",
        "currency",
        "currency_type",
    }

    if not required.issubset(context_data.columns):
        raise ValueError("Missing monetary context")

    if min_group_size < 20:
        raise ValueError("Minimum group size must be at least 20")

    context = context_data.reset_index(drop=True).copy()

    companies = context["company_code"].astype("string")
    currencies = (
        context["currency"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    currency_types = pd.to_numeric(
        context["currency_type"],
        errors="coerce",
    )

    if not companies.str.fullmatch(
        r"TKN_[0-9a-f]{32}"
    ).fillna(False).all():
        raise ValueError("Invalid company tokens")

    if not currencies.str.fullmatch(
        r"[A-Z]{3}"
    ).fillna(False).all():
        raise ValueError("Invalid currency")

    if not currency_types.eq(10).all():
        raise ValueError("Expected currency_type 10")

    matrix = (
        training_matrix
        .reset_index(drop=True)
        .apply(pd.to_numeric, errors="coerce")
    )

    if not np.isfinite(
        matrix.to_numpy(dtype=float)
    ).all():
        raise ValueError("Invalid training values")

    groups = pd.DataFrame({
        "company_code": companies,
        "currency": currencies,
    })

    grouped_positions = groups.groupby(
        ["company_code", "currency"],
        sort=True,
    ).indices

    results = []

    for positions in grouped_positions.values():
        if len(positions) < min_group_size:
            raise ValueError(
                "Group has insufficient training records"
            )

        group_matrix = (
            matrix.iloc[positions]
            .reset_index(drop=True)
        )

        model = train_isolation_forest(
            group_matrix,
            random_state=random_state,
        )

        scores = build_anomaly_scores(
            model,
            group_matrix,
        )

        scores["anomaly_score_percentile"] = (
            scores["anomaly_score"]
            .rank(method="average", pct=True)
            .mul(100)
        )

        scores["model_group_size"] = len(positions)
        scores["_row_position"] = positions

        results.append(scores)

    combined = pd.concat(
        results,
        ignore_index=True,
    )

    return (
        combined
        .sort_values("_row_position")
        .drop(columns="_row_position")
        .reset_index(drop=True)
    )


#Resultado
def build_results(context_data, scores):

    context = (
        context_data[
            CONTEXT_COLUMNS
        ]
        .reset_index(drop=True)
    )
    
    results = pd.concat(
        [
            context,
            scores.reset_index(drop=True),
        ],
        axis=1
    )

    return results

#Pipeline principal
def main():

    print("\nKE24 - Isolation Forest Baseline\n")

    if not TRAINING_FILE.exists():
        raise FileNotFoundError(
            f"Matriz de treinamento não encontrada: {TRAINING_FILE}"
        )

    if not CONTEXT_FILE.exists():
        raise FileNotFoundError(
            f"Dataset de contexto não encontrado: {CONTEXT_FILE}"
        )

    training_matrix = pd.read_parquet(TRAINING_FILE)
    context_data = pd.read_parquet(CONTEXT_FILE)

    validate_inputs(training_matrix, context_data)

    print(
        f"Registros utilizados: {len(training_matrix):,}"
    )

    print(
        f"Features utilizadas: {len(training_matrix.columns):,}" 
    )

    model = train_isolation_forest(training_matrix)
    scores = build_anomaly_scores(model, training_matrix)
    results = build_results(context_data, scores)

    anomaly_count = int(
        results["anomaly_flag"].sum()
    )

    anomaly_percentage = (
        anomaly_count
        / len(results)
        * 100
    )

    results.to_parquet(
        OUTPUT_FILE,
        index=False
    )

    results.to_csv(
        OUTPUT_CSV,
        index=False,
        encoding="utf-8-sig"
    )

    print(
        f"\nAnomalias sinalizadas: {anomaly_count:,}"
    )

    print(
        f"Percentual sinalizado: {anomaly_percentage:.2f}%"
    )

    print("\nTop 10 registros com maior anomaly_score: \n")

    top_anomalies = (
        results
        .sort_values(
            "anomaly_score",
            ascending = False
        )
        .head(10)
    )

    display_columns = [
        "period_key",
        "profit_center",
        "product",
        "reference_document",
        "anomaly_score",
        "anomaly_flag",
        "anomaly_rank"
    ]

    available_columns = [
        column 
        for column in display_columns
        if column in top_anomalies.columns
    ]

    print(
        top_anomalies[
            available_columns
        ]
        .to_string(index=False)
    )

    print("\nResultados gerados em: ")
    print(OUTPUT_FILE)
    print(OUTPUT_CSV)

if __name__ == "__main__":
    main()