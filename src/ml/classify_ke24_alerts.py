from pathlib import Path
import json

import pandas as pd

#configuração
PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_business_validation.csv"
)

RULES_FILE = (
    PROJECT_ROOT
    / "config"
    / "ke24_alert_rules.json"
)

OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_alert_classification.csv"
)

#Carregamento das regras
def load_rules():
    if not RULES_FILE.exists():
        raise FileNotFoundError(f"Arquivo de regras não encontrado: {RULES_FILE}")

    with open(
        RULES_FILE,
        "r",
        encoding="utf-8"
    ) as file:
        rules = json.load(file)

    return rules 

#Validação
def validate_dataset(dataset):

    required_columns = [
        "company_code",
        "currency_type",
        "currency",
        "anomaly_rank",
        "anomaly_score",
        "anomaly_score_percentile",
        "materiality_percentile",
        "stability_rate",
        "total_absolute_value",
        "reference_document"
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in dataset.columns
    ]

    if missing_columns:
        raise ValueError(
            "Colunas obrigatórias ausentes: "
            +", ".join(missing_columns)
        )

def validate_currency_basis(dataset, rules):

    expected_currency_type = (
        rules[
            "currency_basis"
        ][
            "currency_type"
        ]
    )

    invalid_rows = dataset[
        dataset["currency_type"]
        .ne(expected_currency_type)
    ]

    if not invalid_rows.empty:
        invalid_values = (
            invalid_rows[
                "currency_type"
            ]
            .drop.duplicates()
            .tolist()
        )

        raise ValueError(
            "Foram encontrados currency_type diferentes do esperado: "
            +", ".join(
                str(value)
                for value in invalid_values
            )
        )

#Classificação de materialidade
def classify_materiality(row, rules):

    company_code = str(
        row["company_code"]
    )

    company_rules = (
        rules[
            "business_thresholds_by_company"
        ]
        .get(company_code)
    )

    if company_rules is None:
        return(
            "PENDING_COMPANY_RULE",
            "company_not_configured"
        )

    configured_currency = (company_rules.get("currency"))

    row_currency = str(
        row["currency"]
    )

    if configured_currency != row_currency:
        return (
            "PENDING_COMPANY_RULE",
            "currency_mismatch"
        )

    medium_min = (company_rules.get(
        "materiality_medium_min"
    ))

    high_min = (company_rules.get(
        "materiality_high_min"
    ))

    if (
        medium_min is None 
        or high_min is None
    ):
        return (
            "PENDING_BUSINESS_RULE",
            "materiality_thresholds_not_defined"
        )

    if high_min <= medium_min:
        raise ValueError(
            f"Configuração inválida para {company_code}: materiality_high_min deve ser maior que materiality_medium_min."
        )

    value = float(row["total_absolute_value"])

    if value >= high_min:
        return (
            "HIGH",
            "high_materiality"
        )

    if value >= medium_min:
        return (
            "MEDIUM",
            "medium_materiality"
        )

    return (
        "LOW",
        "low_materiality"
    )

#Contrução da classificação
def build_classification(dataset, rules):
    result = dataset.copy()

    classifications = result.apply(
        lambda row: classify_materiality(row, rules), axis=1
    )

    result[
        "materiality_level"
    ] = [
        item[0]
        for item in classifications
    ]

    result[
        "classification_reason"
    ] = [
        item[1]
        for item in classifications
    ]

    return result

#Resumo
def print_summary(result):
    print("\nStatus da classificação: \n")

    summary = (
        result[
            "materiality_level"
        ]
        .value_counts(
            dropna=False
        )
    )

    print(
        summary.to_string()
    )

    print("\nEmpresas encontradas: \n")
    company_summary = (
        result[
            [
                "company_code",
                "currency_type",
                "currency"
            ]
        ]
        .drop_duplicates()
        .sort_values("company_code")
    )

    print(
        company_summary
        .to_string(index=False)
    )

#Pipeline Principal
def main():

    print("\nKE24 - Classificação dos Alertas\n")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(F"Dataser de validação não encontrado: {INPUT_FILE}")

    dataset = pd.read_csv(
        INPUT_FILE,
        encoding="utf-8-sig"
    )

    rules = load_rules()

    validate_dataset(dataset)
    validate_currency_basis(dataset, rules)

    result = build_classification(dataset, rules)

    result.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print(f"Registros processados: {len(result):,}")
    print_summary(result)

    print("\nResultado gerado em: ")
    print(OUTPUT_FILE)

if __name__ == "__main__":
    main()
