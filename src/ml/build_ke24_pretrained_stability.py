import pandas as pd

from src.ml.evaluate_ke24_isolation_forest_stability import (
    RANDOM_STATES,
)

from src.ml.ke24_grouped_model_runtime import (
    score_new_records,
)


IDENTITY_COLUMNS = [
    "_source_file",
    "_source_row_number",
]

CONTEXT_COLUMNS = IDENTITY_COLUMNS + [
    "period_key",
    "profit_center",
    "product",
    "reference_document",
    "company_code",
    "currency",
    "currency_type",
]


def build_pretrained_stability(
    models_by_seed,
    new_matrix,
    new_context,
):
    expected_seeds = set(RANDOM_STATES)

    if (
        not isinstance(models_by_seed, dict)
        or set(models_by_seed) != expected_seeds
    ):
        raise ValueError(
            "Expected one pretrained model per stability seed"
        )

    missing = [
        col
        for col in CONTEXT_COLUMNS
        if col not in new_context.columns
    ]

    if missing:
        raise ValueError(
            "Missing stability context: "
            + ", ".join(missing)
        )

    context = new_context.reset_index(drop=True)

    if context.duplicated(
        subset=IDENTITY_COLUMNS
    ).any():
        raise ValueError("Duplicate record identities")

    result = context[CONTEXT_COLUMNS].copy()

    group_columns = [
        "company_code",
        "currency",
    ]

    score_columns = []
    flag_columns = []
    rank_columns = []

    for seed in RANDOM_STATES:
        trained = models_by_seed[seed]

        # Inference only: no model.fit() is called.
        scored = score_new_records(
            trained,
            new_matrix,
            new_context,
        )

        score_col = f"score_seed_{seed}"
        flag_col = f"flag_seed_{seed}"
        rank_col = f"rank_seed_{seed}"

        result[score_col] = scored[
            "anomaly_score"
        ].to_numpy()

        # Each model uses its own training P95.
        result[flag_col] = scored[
            "reference_p95_flag"
        ].to_numpy()

        # Ranking is descriptive within this batch.
        result[rank_col] = (
            result
            .groupby(group_columns)[score_col]
            .rank(
                method="first",
                ascending=False,
            )
            .astype(int)
        )

        score_columns.append(score_col)
        flag_columns.append(flag_col)
        rank_columns.append(rank_col)

    result["mean_anomaly_score"] = (
        result[score_columns].mean(axis=1)
    )

    result["score_std"] = (
        result[score_columns].std(axis=1)
    )

    result["flag_count"] = (
        result[flag_columns]
        .sum(axis=1)
        .astype(int)
    )

    result["mean_rank"] = (
        result[rank_columns].mean(axis=1)
    )

    result["stability_rate"] = (
        result["flag_count"]
        .div(len(RANDOM_STATES))
        .mul(100)
    )

    result["stability_rank"] = (
        result
        .groupby(group_columns)["mean_rank"]
        .rank(method="first")
        .astype(int)
    )

    return result
