import hashlib

import numpy as np
import pandas as pd
import argparse

from sklearn.metrics import (
    confusion_matrix,
    precision_recall_fscore_support,
)

from src.pipeline.run_ke24_synthetic import load_json

from src.ml.evaluate_ke24_synthetic_holdout import (
    generate_normal_data,
    prepare_features,
)

from src.ml import prepare_ke24_training_matrix as training
from src.ml import train_ke24_isolation_forest as detection


TRAIN_SEEDS = range(11, 21)
TEST_SEEDS = range(29, 39)

GROUP_COLUMNS = ["company_code", "currency"]

RELATIVE_FEATURES = [
    "discount_to_revenue_ratio",
    "material_cost_to_revenue_ratio",
]

def add_relative_features(dataset):
    result = dataset.copy()

    required = {
        "revenue",
        "qty_discount",
        "c_4rm_copper_wire",
    }

    missing = required - set(result.columns)

    if missing:
        raise ValueError(
            f"Missing financial features: {sorted(missing)}"
        )

    revenue = pd.to_numeric(
        result["revenue"], errors="raise"
    ).abs()

    discount = pd.to_numeric(
        result["qty_discount"], errors="raise"
    ).abs()

    material_cost = pd.to_numeric(
        result["c_4rm_copper_wire"], errors="raise"
    ).abs()

    # Avoid division by zero in synthetic records.
    denominator = revenue.clip(lower=1.0)

    result["discount_to_revenue_ratio"] = (
        discount / denominator
    )

    result["material_cost_to_revenue_ratio"] = (
        material_cost / denominator
    )

    if not np.isfinite(
        result[RELATIVE_FEATURES].to_numpy(
            dtype=float
        )
    ).all():
        raise ValueError("Invalid relative features")

    return result

ANOMALY_VALUES = {
    "extreme": {
        "brl_revenue": 80000.0,
        "brl_cost": -45000.0,
        "usd_discount": -25000.0,
        "usd_revenue": 200000.0,
    },
    "moderate": {
        "brl_revenue": 1800.0,
        "brl_cost": -180.0,
        "usd_discount": -90.0,
        "usd_revenue": 1700.0,
    },
    "subtle": {
        "brl_revenue": 1000.0,
        "brl_cost": -95.0,
        "usd_discount": -39.0,
        "usd_revenue": 1020.0,
    },
}

def prepare_batch(schema, seed, prefix):
    name = f"{prefix}_{seed}.xlsx"

    dataset = generate_normal_data(
        schema, seed, name
    )

    # Unique fictional source fingerprint
    dataset["_source_file_sha256"] = (
        hashlib.sha256(
            name.encode("ascii")
        ).hexdigest()
    )

    return dataset



def inject_known_anomalies(
    dataset,
    seed,
    difficulty="extreme",
):
    if difficulty not in ANOMALY_VALUES:
        raise ValueError("Invalid difficulty level")

    values = ANOMALY_VALUES[difficulty]

    result = dataset.copy()
    rng = np.random.default_rng(seed + 1000)

    brl_positions = rng.choice(
        np.arange(0, 20),
        size=2,
        replace=False,
    )

    usd_positions = rng.choice(
        np.arange(20, 40),
        size=2,
        replace=False,
    )

    scenarios = [
        (
            int(brl_positions[0]),
            "revenue",
            values["brl_revenue"],
        ),
        (
            int(brl_positions[1]),
            "c_4rm_copper_wire",
            values["brl_cost"],
        ),
        (
            int(usd_positions[0]),
            "qty_discount",
            values["usd_discount"],
        ),
        (
            int(usd_positions[1]),
            "revenue",
            values["usd_revenue"],
        ),
    ]

    
    known_rows = {}

    case_names = [
        "BRL_high_revenue",
        "BRL_high_cost",
        "USD_high_discount",
        "USD_high_revenue",
    ]

    for (index, column, value), case in zip(
        scenarios,
        case_names,
        strict=True,
    ):
        result.loc[index, column] = value

        source_row = int(
            result.loc[index, "_source_row_number"]
        )

        known_rows[source_row] = case

    return result, known_rows

def calculate_metrics(dataset, column):
    expected = dataset["known_anomaly"]
    predicted = dataset[column]

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

    print(
        f"{column}: "
        f"TP={tp} FP={fp} FN={fn} TN={tn} "
        f"precision={precision:.4f} "
        f"recall={recall:.4f} "
        f"f1={f1:.4f}"
    )


def main(difficulty="extreme", feature_set="baseline"):
    schema = load_json("ke24_schema.json")
    policy = load_json("ke24_privacy_policy.json")

    # Training uses only normal fictional records.
    train_raw = pd.concat(
        [
            prepare_batch(schema, seed, "train")
            for seed in TRAIN_SEEDS
        ],
        ignore_index=True,
    )

    train_features, manifest = prepare_features(
        train_raw, schema, policy
    )

    if feature_set == "ratios":
        train_features = add_relative_features(
            train_features
        )

    selected = training.get_selected_features(manifest)

    if feature_set == "ratios":
        selected = selected + RELATIVE_FEATURES

    train_matrix = training.build_training_matrix(
        train_features, selected
    )

    # Feature selection based on training data only.
    feature_names = train_matrix.columns[
        train_matrix.nunique(dropna=False).gt(1)
    ]

    if len(feature_names) == 0:
        raise ValueError("No training features")

    
    print("\nFINANCIAL FEATURE CHECK")

    for feature in (
        "revenue",
        "c_4rm_copper_wire",
        "qty_discount",
        "total_signed_value",
        "total_absolute_value",
        "max_absolute_value",
    ):
        included = feature in feature_names

        print(f"{feature}: {included}")

        if not included:
            raise ValueError(
                f"Required financial feature missing: {feature}"
            )

    train_matrix = train_matrix[feature_names]

    models = {}

    for key, positions in train_features.groupby(
        GROUP_COLUMNS
    ).indices.items():
        group_matrix = train_matrix.iloc[positions]

        if len(group_matrix) != 200:
            raise ValueError("Unexpected training group size")

        model = detection.train_isolation_forest(
            group_matrix
        )

        train_scores = -model.score_samples(
            group_matrix
        )

        threshold = float(
            np.quantile(train_scores, 0.95)
        )

        models[key] = (model, threshold)

    records = []

    # Independent evaluation batches.
    for seed in TEST_SEEDS:
        raw = prepare_batch(
            schema, seed, "test"
        )

        raw, known_rows = inject_known_anomalies(
            raw, seed, difficulty
        )

        test_features, _ = prepare_features(
            raw, schema, policy
        )

        if feature_set == "ratios":
            test_features = add_relative_features(
                test_features
            )

        test_matrix = training.build_training_matrix(
            test_features, selected
        )[feature_names]

        groups = test_features.groupby(
            GROUP_COLUMNS
        ).indices

        if set(groups) != set(models):
            raise ValueError("Unexpected test groups")

        for key, positions in groups.items():
            model, threshold = models[key]

            group_matrix = test_matrix.iloc[positions]

            scores = -model.score_samples(
                group_matrix
            )

            automatic_flags = (
                model.predict(group_matrix) == -1
            )

            p95_flags = scores >= threshold

            source_rows = (
                test_features.iloc[positions][
                    "_source_row_number"
                ]
                .astype(int)
                .to_numpy()
            )

            for row, score, auto, p95 in zip(
                source_rows,
                scores,
                automatic_flags,
                p95_flags,
                strict=True,
            ):
                records.append({
                    "seed": seed,
                    "source_row": int(row),
                    "currency": key[1],
                    "anomaly_case": known_rows.get(
                        int(row), "normal"
                    ),
                    "known_anomaly": int(row) in known_rows,
                    "anomaly_flag": bool(auto),
                    "train_p95": bool(p95),
                    "anomaly_score": float(score),
                    "train_threshold": float(threshold),
                    "score_margin": float(score - threshold)
                })

    result = pd.DataFrame(records)

    if len(result) != 400:
        raise ValueError("Expected 400 test records")

    if int(result["known_anomaly"].sum()) != 40:
        raise ValueError("Expected 40 known anomalies")

    print("KE24 - MULTIPLE HOLDOUT EVALUATION")
    print("Difficulty:", difficulty)
    print("Training records:", len(train_matrix))
    print("Test records:", len(result))
    print(
        "Known anomalies:",
        int(result["known_anomaly"].sum()),
    )

    print("\nOVERALL")
    calculate_metrics(result, "anomaly_flag")
    calculate_metrics(result, "train_p95")
    
    print("\nRECALL BY ANOMALY TYPE")

    known = result.loc[result["known_anomaly"]]

    for case, group in known.groupby("anomaly_case"):
        total = len(group)

        auto_detected = int(
            group["anomaly_flag"].sum()
        )

        p95_detected = int(
            group["train_p95"].sum()
        )

        print(f"\nCase: {case}")
        print(f"Known anomalies: {total}")

        print(
            f"Isolation Forest: {auto_detected}/{total} "
            f"recall={auto_detected / total:.4f}"
        )

        print(
            f"Training P95: {p95_detected}/{total} "
            f"recall={p95_detected / total:.4f}"
        )
    
    print("\nSCORE MARGIN BY ANOMALY TYPE")

    known = result.loc[
        result["known_anomaly"]
    ]

    for case, group in known.groupby("anomaly_case"):
        margin = group["score_margin"]

        print(f"\nCase: {case}")
        print(
            f"P95 detected: "
            f"{int(group['train_p95'].sum())}/{len(group)}"
        )
        print(f"Minimum margin: {margin.min():.4f}")
        print(f"Median margin:  {margin.median():.4f}")
        print(f"Maximum margin: {margin.max():.4f}")

    print("\nBY CURRENCY")

    for currency, group in result.groupby("currency"):
        print("Currency:", currency)
        calculate_metrics(group, "anomaly_flag")
        calculate_metrics(group, "train_p95")

    print("\nP95 BY TEST BATCH")

    for seed, group in result.groupby("seed"):
        tp = int(
            (
                group["known_anomaly"]
                & group["train_p95"]
            ).sum()
        )

        fp = int(
            (
                ~group["known_anomaly"]
                & group["train_p95"]
            ).sum()
        )

        print(f"Seed {seed}: TP={tp}, FP={fp}")

    print("\nMULTIPLE HOLDOUT EVALUATION COMPLETED")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--difficulty",
        choices=sorted(ANOMALY_VALUES),
        default="extreme",
    )

    parser.add_argument(
        "--feature-set",
        choices=["baseline", "ratios"],
        default="baseline",
    )

    args = parser.parse_args()

    main(
        difficulty=args.difficulty,
        feature_set=args.feature_set,
    )
