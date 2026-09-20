from pathlib import Path

import numpy as np
import pandas as pd

#Configurações
PROJECT_ROOT = Path(__file__).resolve().parents[2]

ML_FEATURES_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_ml_features.parquet"
)

MANIFEST_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_ml_feature_manifest.csv"
)

ANOMALY_RESULTS_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_anomaly_results.parquet"
)

OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_anomaly_explanations.csv"
)

IDENTIFY_COLUMNS = [
    "_load_id",
    "_source_file_sha256",
    "_source_file",
    "_source_row_number",
]

CONTEXT_COLUMNS = [
    "period_key",
    "company_code",
    "profit_center",
    "division",
    "product",
    "record_type",
    "reference_document"
]

TOP_FEATURES_PER_ANOMALY = 5

#Seleção das feaures utilizadas pelo modelo
def get_selected_features(manifest):
    selected_mask = (
        manifest["selected_for_model"]
        .astype("string")
        .str.lower()
        .eq("true")
    )

    selected_features = (
        manifest.loc[
            selected_mask,
            "feature"
        ]
        .tolist()
    )

    if not selected_features:
        raise ValueError(
            "Nenhuma feature selecionada foi encontrada no manifesto"
        )

    return selected_features

#Validação
def validate_inputs(ml_features, anomaly_results, selected_features):

    requires_ml_columns = (
        IDENTIFY_COLUMNS
        + selected_features
    )

    missing_ml_columns = [
        column 
        for column in requires_ml_columns
        if column not in ml_features.columns
    ]

    if missing_ml_columns:
        raise ValueError(
            "Colunas ausentes no dataset de ML: "
            + ", ".join(missing_ml_columns)
        )

    required_result_columns = (
        IDENTIFY_COLUMNS
        + CONTEXT_COLUMNS
        + [
            "anomaly_score",
            "anomaly_flag",
            "anomaly_rank",
        ]
    )

    missing_result_columns = [
        column
        for column in required_result_columns
        if column not in anomaly_results.columns
    ]

    if missing_result_columns:
        raise ValueError(
            "Colunas ausentes nos resultados de anomalia: "
            +", ".join(missing_result_columns)
        )

#Estatísticas de referência
def build_feature_statistics(ml_features, selected_features):

    matrix = (
        ml_features[
            selected_features
        ]
        .apply(
            pd.to_numeric,
            errors="coerce"
        )
    )

    if matrix.isna().any().any():
        raise ValueError("Existem valores nulos nas features utilizadas para análise.")

    if not np.isfinite(
        matrix.to_numpy(dtype="float64")
    ).all():
        raise ValueError(
            "Existem valores infinitos nas features utilizadas para análise"
        )

    statistics = pd.DataFrame({
        "feature": selected_features,

        "feature_mean": [
            matrix[column].mean()
            for column in selected_features
        ],

        "feature_std": [
            matrix[column].std(ddof=0)
            for column in selected_features
        ],

        "non_zero_count": [
            int(
                matrix[column]
                .ne(0)
                .sum()
            )
            for column in selected_features
        ],
    })

    return matrix, statistics

#Explicação dos registros analisados
def build_anomaly_explanations(ml_features, anomaly_results, selected_features, matrix, statistics):

    flagged = (
        anomaly_results[
            anomaly_results[
                "anomaly_flag"
            ]
        ]
        .copy()
    )

    if flagged.empty:
        raise ValueError("Nenhum registro foi sinalizado como anomalia")

    feature_data = (
        ml_features[
            IDENTIFY_COLUMNS
            + selected_features
        ]
        .copy()
    )

    flagged = flagged.merge(
        feature_data,
        on=IDENTIFY_COLUMNS,
        how="left",
        validate="one_to_one"
    )

    statistics_indexed = (
        statistics
        .set_index("feature")
    )

    explanation_rows = []

    for _, row in flagged.iterrows():

        row_explanations= []

        for feature in selected_features:

            feature_value = float(
                row[feature]
            )

            feature_mean = float(
                statistics_indexed.loc[
                    feature,
                    "feature_mean"
                ]
            )

            feature_std = float(
                statistics_indexed.loc[
                    feature,
                    "feature_std"
                ]
            )

            non_zero_count = int(
                statistics_indexed.loc[
                    feature,
                    "non_zero_count"
                ]
            )

            if feature_std > 0:
                standardized_deviation = (
                    feature_value
                    - feature_mean
                ) / feature_std
            else: 
                standardizes_deviation = 0.0

            absolute_deviation = abs(
                standardized_deviation
            )

            if standardized_deviation > 0:
                direction = "above_mean"

            elif standardized_deviation < 0:
                direction = "below_mean"

            else:
                direction = "at_mean"

            explanation = {
                "anomaly_rank": int(row["anomaly_rank"]),
                "anomaly_score": float(row["anomaly_score"]),
                "period_key": row["period_key"],
                "company_code": row["company_code"],
                "profit_center": row["profit_center"],
                "division": row["division"],
                "product": row["product"],
                "record_type": row["record_type"],
                "reference_document": row["reference_document"],
                "_source_file": row["_source_file"],
                "_source_row_number": row["_source_row_number"],
                "feature": feature,
                "feature_value": feature_value,
                "feature_mean": feature_mean,
                "feature_std": feature_std,
                "non_zero_count": non_zero_count,
                "standardized_deviation": standardized_deviation,
                "absolute_standardized_deviation": absolute_deviation,
                "direction": direction,
            }

            row_explanations.append(explanation)

        row_explanations = sorted(
            row_explanations,
            key=lambda item: item[
                "absolute_standardized_deviation"
            ],
            reverse=True
        )

        explanation_rows.extend(
            row_explanations[
                :TOP_FEATURES_PER_ANOMALY
            ]
        )

    return pd.DataFrame(explanation_rows)   

#Exibição dos resultados
def print_anomaly_explanations(explanations):

    anomaly_ranks = sorted(
        explanations[
            "anomaly_rank"
        ].unique()
    )

    for anomaly_rank in anomaly_ranks:
        anomaly = (
            explanations[
                explanations[
                    "anomaly_rank"
                ] == anomaly_rank
            ]
            .copy()
        )

        first_row = (
            anomaly.iloc[0]
        )

        print("\n----------------------------------------")
        print(f"Anomalia rank: {anomaly_rank}")
        print(f"Período: {first_row['period_key']}")
        print(f"Profit center: {first_row['profit_center']}")
        print(f"Produto: {first_row['product']}")
        print(f"Documento de referência: {first_row['reference_document']}")
        print(f"Anomaly score: {first_row['anomaly_score']:.6f}")

        print("\nTop features mais extremas:\n")
        print(
            anomaly[
                [
                    "feature",
                    "feature_value",
                    "feature_mean",
                    "standardized_deviation",
                    "non_zero_count",
                ]
            ]
            .to_string(
                index=False
            )
        )

#Pipeline principal
def main():

    print("\nKE24 - Análise dos Alertas de Anomalia\n")

    required_files = [
        ML_FEATURES_FILE,
        MANIFEST_FILE,
        ANOMALY_RESULTS_FILE,
    ]

    for file_path in required_files:
        if not file_path.exists():
            raise FileNotFoundError(
                f"Arquivo não encontrado: {file_path}"
            )

    ml_features = pd.read_parquet(ML_FEATURES_FILE)

    manifest = pd.read_csv(
        MANIFEST_FILE,
        encoding="utf-8-sig"
    )

    anomaly_results = pd.read_parquet(ANOMALY_RESULTS_FILE)

    selected_features = (
        get_selected_features(manifest)
    )

    validate_inputs(
        ml_features,
        anomaly_results,
        selected_features
    )

    matrix, statistics = (
        build_feature_statistics(
            ml_features,
            selected_features
        )
    )

    explanations = (
        build_anomaly_explanations(
            ml_features,
            anomaly_results,
            selected_features,
            matrix,
            statistics
        )
    )

    explanations.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    flagged_count = (
        anomaly_results[
            "anomaly_flag"
        ].sum()
    )

    print(f"Registros sinalizados analisados: {int(flagged_count):,}")
    print(f"Features utilizadas pelo modelo: {len(selected_features):,}")

    print_anomaly_explanations(explanations)
    print("\n----------------------------------------")
    print("\nExplicações geradas em: ")
    print(OUTPUT_FILE)
   
if __name__ == "__main__":
    main()