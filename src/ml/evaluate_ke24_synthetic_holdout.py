import numpy as np
import pandas as pd
import hashlib

from sklearn.metrics import confusion_matrix
from sklearn.metrics import precision_recall_fscore_support

from src.pipeline.run_ke24_synthetic import (
    generate_synthetic_data,
    load_json,
    TEST_KEY,
)
from src.ml.evaluate_ke24_synthetic_quality import SCENARIOS
from src.privacy.anonymize_ke24 import anonymize_ke24_dataframe
from src.processing import prepare_ke24_processed as processing
from src.features import build_ke24_ml_features as features
from src.ml import prepare_ke24_training_matrix as training
from src.ml import train_ke24_isolation_forest as detection


def generate_normal_data(schema, seed, source_name):
    rng = np.random.default_rng(seed)
    data = generate_synthetic_data(schema)

    # Normal fictional financial behavior
    data["revenue"] = rng.integers(500, 950, len(data)).astype(float)
    data["c_4rm_copper_wire"] = (
        -rng.integers(40, 90, len(data)).astype(float)
    )
    data["qty_discount"] = (
        -rng.integers(5, 35, len(data)).astype(float)
    )
    data["sales_quantity"] = (
        rng.integers(1, 6, len(data)).astype(float)
    )

    # Separate origins for training and testing
    data["_source_file"] = source_name
    data["_source_file_sha256"] = (
        "a" * 64 if seed == 11 else "b" * 64
    )

    data["reference_document"] = [
        f"{source_name}_DOC_{i:03d}"
        for i in range(len(data))
    ]

    return data


def prepare_features(raw, schema, policy):
    protected = anonymize_ke24_dataframe(
        raw, policy, schema, TEST_KEY
    )

    processing.validate_input_data(protected)

    processed, _ = processing.standardize_measure_types(
        protected.copy()
    )
    processed = processing.add_period_key(processed)
    processed = processing.sort_processed_data(processed)

    measure_columns = features.get_measure_columns(processed)
    measures = features.prepare_measure_matrix(
        processed, measure_columns
    )

    active, _ = features.select_active_measures(measures)
    engineered = features.build_engineered_features(measures)

    dataset = pd.concat(
        [
            processed[features.CONTEXT_COLUMNS].reset_index(drop=True),
            measures[active].reset_index(drop=True),
            engineered.reset_index(drop=True),
        ],
        axis=1,
    )

    manifest = features.build_feature_manifest(
        measure_columns,
        active,
        list(engineered.columns),
    )

    return dataset, manifest


def calculate_metrics(result, prediction_column):
    expected = result["known_anomaly"].astype(bool)
    predicted = result[prediction_column].astype(bool)

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
    print(f"TP={tp} FP={fp} FN={fn} TN={tn}")
    print(f"Precision: {precision:.4f}")
    print(f"Recall:    {recall:.4f}")
    print(f"F1-score:  {f1:.4f}")


def main():
    schema = load_json("ke24_schema.json")
    policy = load_json("ke24_privacy_policy.json")

    
    # Ten synthetic training batches:
    # 400 total records, 200 per company/currency.
    train_batches = []

    for batch_number in range(10):
        seed = 11 + batch_number
        source_name = (
            f"synthetic_train_{batch_number:02d}.xlsx"
        )

        batch = generate_normal_data(
            schema,
            seed,
            source_name,
        )

        # Deterministic fictional source identifier.
        batch["_source_file_sha256"] = (
            hashlib.sha256(
                f"{source_name}:{seed}".encode("ascii")
            ).hexdigest()
        )

        train_batches.append(batch)

    train_raw = pd.concat(
        train_batches,
        ignore_index=True,
    )

    test_raw = generate_normal_data(
        schema, 29, "synthetic_test.xlsx"
    )

    known_rows = set()

    # Inject anomalies only in the test dataset
    for index, (column, value, _) in SCENARIOS.items():
        test_raw.loc[index, column] = value
        known_rows.add(
            int(test_raw.loc[index, "_source_row_number"])
        )

    train_features, manifest = prepare_features(
        train_raw, schema, policy
    )
    test_features, _ = prepare_features(
        test_raw, schema, policy
    )

    # Select features using training data only
    selected = training.get_selected_features(manifest)

    train_matrix = training.build_training_matrix(
        train_features, selected
    )
    test_matrix = training.build_training_matrix(
        test_features, selected
    )

    nonconstant = train_matrix.nunique(dropna=False).gt(1)
    feature_names = train_matrix.columns[nonconstant]

    if len(feature_names) == 0:
        raise ValueError("No training features")

    train_matrix = train_matrix[feature_names]
    test_matrix = test_matrix[feature_names]

    # Group using protected company identifiers and local currency
    group_columns = ["company_code", "currency"]

    train_groups = train_features.groupby(group_columns).indices
    test_groups = test_features.groupby(group_columns).indices

    if set(train_groups) != set(test_groups):
        raise ValueError("Training and testing groups differ")

    records = []

    for group_key in sorted(train_groups):
        train_positions = train_groups[group_key]
        test_positions = test_groups[group_key]

        if len(train_positions) < 20:
            raise ValueError("Insufficient training records")

        train_group = train_matrix.iloc[train_positions]
        test_group = test_matrix.iloc[test_positions]

        # Fit only on training records
        model = detection.train_isolation_forest(train_group)

        train_scores = -model.score_samples(train_group)
        test_scores = -model.score_samples(test_group)

        # Threshold learned exclusively from training scores
        threshold = float(np.quantile(train_scores, 0.95))

        model_flags = model.predict(test_group) == -1
        threshold_flags = test_scores >= threshold

        # Diagnostic: compare training behavior with
        # normal, unseen test documents.
        normal_positions = [
            index
            for index, position in enumerate(test_positions)
            if int(
                test_features.iloc[position]["_source_row_number"]
            ) not in known_rows
        ]

        normal_scores = test_scores[normal_positions]
        normal_flags = model_flags[normal_positions]

        train_flags = (
            model.predict(train_group) == -1
        )

        print(f"\nGROUP: {group_key[1]}")
        print(f"Training records: {len(train_scores)}")
        print(f"Normal test records: {len(normal_scores)}")

        print(
            "Train score median:",
            round(float(np.median(train_scores)), 4),
        )

        print(
            "Normal test score median:",
            round(float(np.median(normal_scores)), 4),
        )

        print(
            "Train P95 threshold:",
            round(threshold, 4),
        )

        print(
            "Train default flags:",
            int(train_flags.sum()),
        )

        print(
            "Normal test default flags:",
            int(normal_flags.sum()),
        )

        print(
            "Normal test above train P95:",
            int((normal_scores >= threshold).sum()),
        )


        for position, score, flag, p95_flag in zip(
            test_positions,
            test_scores,
            model_flags,
            threshold_flags,
        ):
            source_row = int(
                test_features.iloc[position]["_source_row_number"]
            )

            records.append({
                "source_row": source_row,
                "currency": group_key[1],
                "known_anomaly": source_row in known_rows,
                "anomaly_flag": bool(flag),
                "train_p95": bool(p95_flag),
                "anomaly_score": float(score),
                "train_threshold": threshold,
            })

    result = pd.DataFrame(records)

    assert len(result) == 40
    assert int(result["known_anomaly"].sum()) == 4
    assert result.groupby("currency").size().to_dict() == {
        "BRL": 20,
        "USD": 20,
    }

    print("KE24 - INDEPENDENT HOLDOUT EVALUATION")
    print("Training records:", len(train_matrix))
    print("Testing records:", len(test_matrix))
    print("Known anomalies:", int(result["known_anomaly"].sum()))

    print("\nKnown anomaly results:")
    print(
        result.loc[
            result["known_anomaly"],
            [
                "source_row",
                "currency",
                "anomaly_flag",
                "train_p95",
                "anomaly_score",
            ],
        ].sort_values("source_row").to_string(index=False)
    )

    calculate_metrics(result, "anomaly_flag")
    calculate_metrics(result, "train_p95")

    print("\nResults by currency:")

    for currency, group in result.groupby("currency"):
        print(f"\nCurrency: {currency}")
        calculate_metrics(group, "anomaly_flag")
        calculate_metrics(group, "train_p95")

    print("\nHOLDOUT EVALUATION COMPLETED")


if __name__ == "__main__":
    main()
