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

# Contexto dos registros utilizados no treinamento
TRAIN_CONTEXT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_training_context.parquet"
)

# Dados reservados para a validação temporal
VALIDATION_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_validation_features.parquet"
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


# Separação temporal entre treinamento e validação
def split_temporal_by_group(dataset, min_train_records=20):
    group_columns = ["company_code", "currency"]

    required = set(group_columns + ["period_date"])
    missing = required - set(dataset.columns)

    if missing:
        raise ValueError(
            "Colunas ausentes para divisão temporal: "
            + ", ".join(sorted(missing))
        )

    if dataset.empty or dataset[group_columns].isna().any().any():
        raise ValueError(
            "Dataset vazio ou grupos sem identificação."
        )

    dates = pd.to_datetime(
        dataset["period_date"],
        errors="raise"
    )

    if dates.isna().any():
        raise ValueError("Existem datas de período ausentes.")

    # Identifica o último período de cada empresa/moeda
    last_dates = (
        dataset.assign(_split_date=dates)
        .groupby(group_columns)["_split_date"]
        .transform("max")
    )

    # Períodos anteriores: treinamento
    treino = (
        dataset.loc[dates.lt(last_dates)]
        .copy()
        .reset_index(drop=True)
    )

    # Último período: validação
    validacao = (
        dataset.loc[dates.eq(last_dates)]
        .copy()
        .reset_index(drop=True)
    )

    # Confere se todos os grupos possuem dados suficientes
    total_grupos = dataset.groupby(group_columns).ngroups
    treino_por_grupo = treino.groupby(group_columns).size()
    validacao_por_grupo = validacao.groupby(group_columns).size()

    if (
        len(treino_por_grupo) != total_grupos
        or len(validacao_por_grupo) != total_grupos
        or (treino_por_grupo < min_train_records).any()
        or (validacao_por_grupo < 1).any()
        or len(treino) + len(validacao) != len(dataset)
    ):
        raise ValueError(
            "Divisão temporal inviável para um ou mais grupos."
        )

    return treino, validacao

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

    
    # Carrega o dataset completo e o manifesto de features
    dataset = pd.read_parquet(INPUT_FILE)

    manifest = pd.read_csv(
        MANIFEST_FILE,
        encoding="utf-8-sig"
    )

    # Separa treinamento e validação por período
    train_df, validation_df = split_temporal_by_group(dataset)

    print(f"Registros de treinamento: {len(train_df):,}")
    print(f"Registros de validação: {len(validation_df):,}")

    # Recupera as features selecionadas no treinamento
    selected_features = get_selected_features(manifest)

    # Cria a matriz utilizando somente o treinamento
    matrix = build_training_matrix(
        train_df,
        selected_features
    )

    # Calcula o perfil apenas sobre os dados de treinamento
    feature_profile = build_feature_profile(matrix)

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

    # Verifica se matriz e contexto possuem a mesma quantidade de registros, preservando o alinhamento entre eles.
    if len(matrix) != len(train_df):
        raise ValueError(
            "A matriz e o contexto de treinamento "
            "possuem quantidades diferentes de registros."
        )

    # Salva a matriz numérica para o modelo
    matrix.to_parquet(
        OUTPUT_FILE,
        index=False
    )

    # Salva o contexto correspondente ao treinamento
    train_df.to_parquet(
        TRAIN_CONTEXT_FILE,
        index=False
    )

    # Reserva os dados do último período de cada grupo
    validation_df.to_parquet(
        VALIDATION_FILE,
        index=False
    )

    # Salva o perfil das features
    feature_profile.to_csv(
        PROFILE_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print("\nContexto de treinamento salvo.")
    print("Registros de treinamento:", len(train_df))

    print("\nConjunto de validação salvo.")
    print("Registros de validação:", len(validation_df))


    print("\nMatrix de treinamento gerada em: ")
    print(OUTPUT_FILE)

    print("\nPerfil das features gerado em: ")
    print(PROFILE_FILE)

if __name__ == "__main__":
    main()