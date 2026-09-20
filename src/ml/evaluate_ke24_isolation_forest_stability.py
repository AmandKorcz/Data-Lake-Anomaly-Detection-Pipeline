from pathlib import Path

import pandas as pd
from sklearn.ensemble import IsolationForest

#Configurações
PROJECT_ROOT = Path(__file__).resolve().parents[2]

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
    / "ke24_isolated_forest_stability.csv"
)

RANDOM_STATES = [
    7, 21, 42, 84, 123
]

TOP_N = 10

#Pipeline principal
def main():
    print("\nKE24 - Avaliação de Estabilidade do Isolation Forest\n")

    if not TRAINING_FILE.exists():
        raise FileNotFoundError(
            f"Matriz de treinamento não encontrada: {TRAINING_FILE}"
        )

    if not CONTEXT_FILE.exists():
        raise FileNotFoundError(
            f"Dataset de contexto não encontrado: {CONTEXT_FILE}"
        )

    training_matrix = pd.read_parquet(TRAINING_FILE)
    context = pd.read_parquet(CONTEXT_FILE)

    if len(training_matrix) != len(context):
        raise ValueError("A matriz de treinamento e o dataser de contexto possuem quantidades diferentes de registros.")

    result = context[
        [
            "_source_file",
            "_source_row_number",
            "period_key",
            "profit_center",
            "product",
            "reference_document"
        ]
    ].copy()

    score_columns = []
    flag_columns = []

    for random_state in RANDOM_STATES:
        model = IsolationForest(
            n_estimators=300,
            contamination="auto",
            random_state=random_state,
            n_jobs=-1
        )

        model.fit(training_matrix)

        anomaly_score = (
            -model.score_samples(training_matrix)
        )

        prediction = model.predict(training_matrix)

        score_column = (f"score_seed_{random_state}")
        flag_column = (f"flag_seed_{random_state}")

        result[
            score_column
        ] = anomaly_score

        result[
            flag_column
        ] = (
            prediction == -1
        )

        score_columns.append(score_column)
        flag_columns.append(flag_column)

    result["mean_anomaly_score"] = result[score_columns].mean(axis=1)
    result["score_std"] = result[score_columns].std(axis=1)
    result["flag_count"] = result[flag_columns].sum(axis=1)
    result["mean_rank"] = (result[score_columns].rank(
        ascending=False,
        method="average"
    ).mean(axis=1))

    result = result.sort_values(
        [
            "mean_anomaly_score",
            "flag_count",
        ],
        ascending=[False, False,]
    ).reset_index(drop=True)

    result["stability_rank"] = (result.index + 1)

    result.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print(f"Registros avaliados: {len(result):,}")
    print(f"Execuções realizadas: {len(RANDOM_STATES):,}")

    print("\nTop 10 registros por score médio: \n")
    print(
        result[
            [
                "stability_rank",
                "period_key",
                "profit_center",
                "product",
                "reference_document",
                "mean_anomaly_score",
                "score_std",
                "flag_count",
                "mean_rank"
            ]
        ]
        .head(TOP_N)
        .to_string(index=False)
    )

    print("\nResultado de estabilidade gerado em:")
    print(OUTPUT_FILE)

if __name__ == "__main__":
    main()