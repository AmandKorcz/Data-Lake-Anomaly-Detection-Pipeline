from pathlib import Path

import pandas as pd 

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_business_validation.csv"
)

IDENTITY_COLUMNS = [
    "_source_file",
    "_source_row_number"
]

ALLOWED_REVIEW_STATUS = {
    "pending",
    "validated"
}

ALLOWED_BUSINESS_VALIDATION = {
    "TRUE_ANOMALY",
    "EXPECTED_BEHAVIOR",
    "NEEDS_INVESTIGATION"
}

#Validação de estrutura
def validate_structure(dataset):
    required_columns = (
        IDENTITY_COLUMNS
        + [
            "reference_document",
            "review_status",
            "business_validation",
            "validation_reason",
            "validated_by",
            "validated_at",
            "business_notes"
        ]
    )

    missing_columns = [
        column
        for column in required_columns
        if column not in dataset.columns
    ]

    if missing_columns:
        raise ValueError(
            "Colunas obrigatórias ausentes:"
            +", ".join(missing_columns)
        )

    if dataset.duplicated(
        subset=IDENTITY_COLUMNS
    ).any():
        raise ValueError("Existem registros duplicados no dataset de validação")

#Validaçã dos status
def validate_review_status(dataset):

    review_status = (
        dataset["review_status"]
        .astype("string")
        .str.strip()
        .str.lower()
    )

    invalid_status = (
        ~review_status.isin(ALLOWED_REVIEW_STATUS)
    )

    if invalid_status.any():
        invalid_values = (
            dataset.loc[
                invalid_status,
                "review_status"
            ]
            .drop_duplicates()
            .tolist()
        )

        raise ValueError(
            "review_status inválido: "
            +", ".join(
                str(value)
                for value in invalid_values
            )
        )

#Validação dos registros já revisados
def validate_reviwed_records(dataset):
    review_status = (
        dataset["review_status"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    validated = dataset[
        review_status.eq("validated")
    ].copy()

    if validated.empty:
        return

    business_validation = (
        validated["business_validation"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    invalid_business_validation = (
        ~business_validation.isin(
            ALLOWED_BUSINESS_VALIDATION
        )
    )

    if invalid_business_validation.any():
        invalid_values = (
            validated.loc[
                invalid_business_validation,
                "business_validation"
            ]
            .drop_duplicates()
            .to_list()
        )

        raise ValueError(
            "business_validation inválido: "
            +", ".join(
                str(value)
                for value in invalid_values
            )
        )

    required_text_columns = [
        "validation_reason",
        "validated_by"
    ]

    for column in required_text_columns:
        values = (
            validated[column]
            .astype("string")
            .str.strip()
        )

        missing_values = (
            values.isna() | values.eq("")
        )

        if missing_values.any():
            raise ValueError(
                f"Existem registros validados sem {column}"
            )

    validated_dates = pd.to_datetime(
        validated["validated_at"],
        errors="coerce"
    )

    if validated_dates.isna().any():
        raise ValueError("Existem registros validados sem uma validated_at válida.")

#Validação dos registros pendentes
def validate_pending_records(dataset):
    review_status = (
        dataset["review_status"]
        .astype("string")
        .str.strip()
        .str.lower()
    )

    pending = dataset[
        review_status.eq("pending")
    ].copy()

    if pending.empty:
        return

    has_business_validation = (
        pending["business_validation"]
        .notna()
        & pending["business_validation"]
        .astype("string")
        .str.strip()
        .ne("")
    )

    if has_business_validation.any():
        raise ValueError("Existem registros com review_status pending que já possuem business_validation")

#Resumo
def print_summary(dataset):
    review_status = (
        dataset["review_status"]
        .astype("string")
        .str.strip()
        .str.lower()
    )

    pending_count = int(
        review_status.eq("pending").sum()
    )

    validated_count = int(
        review_status.eq("validated").sum()
    )

    print(f"Registros analisados: {len(dataset):,}")
    print(f"Registros pendentes: {pending_count:,}")
    print(f"Registros validados: {validated_count:,}")

    if validated_count > 0:
        validated = dataset[
            review_status.eq("validated")
        ]

        print("\nDistribuição da validação de negócio: \n")

        print(
            validated["business_validation"]
            .value_counts()
            .to_string()
        )

#Pipeline principal
def main():
    print("\nKE24 - Validação da Revisão de Negócio\n")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Dataset de validação não encontrado: {INPUT_FILE}"
        )

    dataset = pd.read_csv(
        INPUT_FILE,
        encoding="utf-8-sig"
    )

    validate_structure(dataset)
    validate_review_status(dataset)
    validate_reviwed_records(dataset)
    validate_pending_records(dataset)

    print_summary(dataset)

    print("\nValidação da revisão de negócio APROVADA!")

if __name__ == "__main__":
    main()
        