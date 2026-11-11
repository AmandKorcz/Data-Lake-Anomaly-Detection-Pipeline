import pandas as pd

from sklearn.metrics import (
    confusion_matrix,
    precision_recall_fscore_support,
)

from src.pipeline.run_ke24_synthetic import (
    generate_synthetic_data,
    load_json,
    TEST_KEY,
)
from src.privacy.anonymize_ke24 import (
    anonymize_ke24_dataframe,
)
from src.privacy.validate_protected_dataset import (
    validate_protected_dataset,
)
from src.processing import prepare_ke24_processed as processing
from src.features import build_ke24_ml_features as features
from src.ml import prepare_ke24_training_matrix as training
from src.ml import train_ke24_isolation_forest as detection


# All indexes refer to fictional generator records.
SCENARIOS = {
    5: ("revenue", 80000.0, "high_revenue"),
    12: ("c_4rm_copper_wire", -45000.0, "high_cost"),
    30: ("qty_discount", -25000.0, "high_discount"),
    39: ("revenue", 200000.0, "high_revenue"),
}


def build_evaluation_dataset():
    schema = load_json("ke24_schema.json")
    policy = load_json("ke24_privacy_policy.json")

    raw = generate_synthetic_data(schema)

    # Labels remain separate from model inputs.
    known_cases = {}

    for index, (column, value, case) in SCENARIOS.items():
        raw.loc[index, column] = value

        row_number = int(
            raw.loc[index, "_source_row_number"]
        )

        known_cases[row_number] = case

    protected = anonymize_ke24_dataframe(
        raw, policy, schema, TEST_KEY
    )

    validate_protected_dataset(
        protected,
        policy,
        require_all_columns=True,
    )

    processing.validate_input_data(protected)

    processed, _ = processing.standardize_measure_types(
        protected.copy()
    )

    processed = processing.add_period_key(processed)
    processed = processing.sort_processed_data(processed)

    processing.validate_processed_data(
        processed, len(protected)
    )

    measure_columns = features.get_measure_columns(processed)

    measures = features.prepare_measure_matrix(
        processed, measure_columns
    )

    active, _ = features.select_active_measures(measures)
    engineered = features.build_engineered_features(measures)

    ml_features = pd.concat(
        [
            processed[features.CONTEXT_COLUMNS].reset_index(drop=True),
            measures[active].reset_index(drop=True),
            engineered.reset_index(drop=True),
        ],
        axis=1,
    )

    features.validate_output(
        processed,
        ml_features,
        list(engineered.columns),
    )

    manifest = features.build_feature_manifest(
        measure_columns,
        active,
        list(engineered.columns),
    )

    selected = training.get_selected_features(manifest)

    matrix = training.build_training_matrix(
        ml_features, selected
    )

    matrix = matrix.loc[
        :, matrix.nunique(dropna=False).gt(1)
    ]

    if matrix.shape[1] == 0:
        raise ValueError("Empty training matrix")

    scores = detection.build_grouped_anomaly_scores(
        matrix, ml_features
    )

    group_sizes = (
        ml_features
        .groupby(["company_code", "currency"])
        .size()
        .tolist()
    )

    if sorted(group_sizes) != [20, 20]:
        raise ValueError("Unexpected synthetic groups")

    source_rows = (
        ml_features["_source_row_number"]
        .astype(int)
        .reset_index(drop=True)
    )

    if source_rows.duplicated().any():
        raise ValueError("Duplicate source rows")

    if not set(known_cases).issubset(set(source_rows)):
        raise ValueError("Known anomalies missing")

    # Labels are introduced only after model training.
    result = pd.DataFrame({
        "source_row": source_rows,
        "currency": ml_features["currency"].to_numpy(),
        "case": [
            known_cases.get(int(row), "normal")
            for row in source_rows
        ],
        "known_anomaly": source_rows.isin(
            known_cases
        ).to_numpy(),
        "anomaly_flag": scores[
            "anomaly_flag"
        ].to_numpy(dtype=bool),
        "p95_candidate": scores[
            "anomaly_score_percentile"
        ].ge(95).to_numpy(dtype=bool),
        "score_percentile": scores[
            "anomaly_score_percentile"
        ].to_numpy(),
    })

    return result


def print_metrics(dataset, prediction_column):
    expected = dataset["known_anomaly"]
    predicted = dataset[prediction_column]

    precision, recall, f1, _ = (
        precision_recall_fscore_support(
            expected,
            predicted,
            average="binary",
            zero_division=0,
        )
    )

    tn, fp, fn, tp = confusion_matrix(
        expected,
        predicted,
        labels=[False, True],
    ).ravel()

    print(f"\nMethod: {prediction_column}")
    print(f"TP: {tp} | FP: {fp} | FN: {fn} | TN: {tn}")
    print(f"Precision: {precision:.4f}")
    print(f"Recall:    {recall:.4f}")
    print(f"F1-score:  {f1:.4f}")


def main():
    result = build_evaluation_dataset()

    print("KE24 - SYNTHETIC MODEL QUALITY")
    print("Records:", len(result))
    print("Known anomalies:", int(result["known_anomaly"].sum()))

    print("\nInjected scenarios:")
    print(
        result.loc[
            result["known_anomaly"],
            [
                "source_row",
                "currency",
                "case",
                "anomaly_flag",
                "p95_candidate",
                "score_percentile",
            ],
        ].to_string(index=False)
    )

    print_metrics(result, "anomaly_flag")
    print_metrics(result, "p95_candidate")

    print("\nEvaluation by currency:")

    for currency, group in result.groupby("currency"):
        print(f"\nCurrency: {currency}")
        print_metrics(group, "anomaly_flag")
        print_metrics(group, "p95_candidate")

    print("\nSYNTHETIC QUALITY EVALUATION COMPLETED")


if __name__ == "__main__":
    main()
