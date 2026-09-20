from pathlib import Path

import numpy as np
import pandas as pd

#Configurações
PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
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

OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_training_matrix.parquet"
)

PROFILE_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_training_feature_profile.csv"
)

#Seleção das features
def get_selected_features(manifest):

    selected_mask = (
        manifest["selected_for_model"]
        .astype("string")
        .str.lower()
        .eq("true")
    )

    selected_features = (
        manifest.loc[
            selected_mask, "feature"
        ]
        .tolist()
    )

    if not selected_features:
        raise ValueError("Nenhuma feature foi selecionada para o modelo.")

    return selected_features

#Preparação da matriz
def build_training_matrix(dataset, selected_features):

    missing_features = [
        feature
        for feature in selected_features
        if feature not in dataset.columns
    ]

    if missing_features:
        raise ValueError(
            "Features selecionadas ausentes no dataset: "
            + ", ".join(missing_features)
        )

    matrix = (
        dataset[selected_features]
        .apply(
            pd.to_numeric,
            errors="coerce"
        )
    )

    if matrix.isna().any().any():
        columns_with_nulls = (
            matrix.columns[
                matrix.isna().any()
            ]
            .tolist()
        )

        raise ValueError(
            "Existem valores nulos na matriz de treinamento: "
            +", ".join(columns_with_nulls)
        )

    finite_mask = np.isfinite(
        matrix.to_numpy(
            dtype="float64"
        )
    )

    if not finite_mask.all():
        raise ValueError("A matrix de treinamento possui valores infinitos.")

    return matrix

#Perfil das features
def build_feature_profile(matrix):

    rows=[]
    for column in matrix.columns:
        values = matrix[column]

        unique_values = int(
            values.nunique(dropna=False)
        )

        rows.append({
            "feature": column,
            "unique_values": unique_values,
            "is_constant": unique_values <= 1,
            "min": values.min(),
            "max": values.max(),
            "mean": values.mean(),
            "std": values.std()
        })

    return pd.DataFrame(rows)

#Pipeline principal
def main():

    print("\nKE24 - Preparação da Matriz de Treinamento\n")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            F"Dataser de ML não encontrado: {INPUT_FILE}"
        )

    if not MANIFEST_FILE.exists():
        raise FileNotFoundError(
            f"Manifesto não encontrado: {MANIFEST_FILE}"
        )

    dataset = pd.read_parquet(INPUT_FILE)
    manifest = pd.read_csv(
        MANIFEST_FILE,
        encoding="utf-8-sig"
    )

    selected_features = (get_selected_features(manifest))
    matrix = (build_training_matrix(dataset, selected_features))
    feature_profile = (build_feature_profile(matrix))
    constant_features = (feature_profile[
        feature_profile["is_constant"]
    ])

    print(
        f"Registros: {len(matrix):,}"
    )

    print(
        f"Features selecionadas: {len(matrix.columns):,}"
    )

    print(
        f"Features constantes: {len(constant_features):,}"
    )

    if not constant_features.empty:
        print("\nFeatures constantes encontradas: ")
        print(
            constant_features[
                ["feature", "unique_values"]
            ]
            .to_string(index=False)
        )

        matrix = matrix.drop(
            columns=constant_features["feature"].tolist()
        )

    print(
        f"\nFeatures finais para treinamento: {len(matrix.columns):,}"
    )

    matrix.to_parquet(
        OUTPUT_FILE,
        index=False
    )

    feature_profile.to_csv(
        PROFILE_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print("\nMatrix de treinamento gerada em: ")
    print(OUTPUT_FILE)

    print("\nPerfil das features gerado em: ")
    print(PROFILE_FILE)

if __name__ == "__main__":
    main()