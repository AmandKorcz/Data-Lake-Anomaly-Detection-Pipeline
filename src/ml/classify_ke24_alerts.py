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
        "reference_document",
        "review_status",
        "business_validation"
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

    currency_type_numeric = pd.to_numeric(
        dataset["currency_type"],
        errors="coerce"
    )

    if currency_type_numeric.isna().any():
        raise ValueError("Existem valores inválidos em currency_type.")

    invalid_rows = (
        currency_type_numeric
        .ne(expected_currency_type)
    )

    if invalid_rows.any():
        invalid_rows = (
            currency_type_numeric.loc[invalid_rows]
            .drop_duplicates()
            .tolist()
        )

        raise ValueError(
            "Foram encontrados currency_tpe diferentes do esperado: "
            +", ".join(
                str(value)
                for value in invalid_rows
            )
        )

#Classificação de materialidade
def classify_materiality(row, rules):

    company_code = (
        str(row["company_code"])
        .strip()
        .upper()
    )

    company_rules = (
        rules.get(
            "business_thresholds_by_company"
        )
        .get(company_code)
    )

    if company_rules is None:
        return(
            "PENDING_COMPANY_RULE",
            "company_not_configured"
        )

    configured_currency = (
        str(
            company_rules.get(
                "currency",
                ""
            )
        )
        .strip()
        .upper()
    )

    row_currency = (
        str(row["currency"])
        .strip()
        .upper()
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

    medium_min = float(medium_min)
    high_min = float(high_min)

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

#Classficação do sinal de anomalia
def classify_anomaly_signal(row, rules):
    technical_rules = (
        rules.get(
            "technical_thresholds",
            {}
        )
    )

    medium_min = (
        technical_rules.get("anomaly_percentile_medium_min")
    )

    high_min = (
        technical_rules.get("anomaly_percentile_high_min")
    )

    if (
        medium_min is None  
        or high_min is None
    ):
        return (
            "PENDING_TECHNICAL_RULE",
            "anomaly_thresholds_not_defined"
        )

    medium_min = float(medium_min)
    high_min = float(high_min)

    if not (
        0 <= medium_min
        < high_min
        <= 100
    ):
        raise ValueError(
            "Os limites de anomaly percentile devem respeitar: 0 <= medium < high <= 100."
        )

    value = float(
        row["anomaly_score_percentile"]
    )

    if value >= high_min:
        return (
            "HIGH",
            "high_anomaly_signal"
        )

    if value >= medium_min:
        return (
            "MEDIUM",
            "medium_anomaly_signal"
        )

    return (
        "LOW",
        "low_anomaly_signal"
    )

#Classificação da estabilidade
def classify_stability(row, rules):
    technical_rules = (
        rules.get(
            "technical_thresholds",
            {}
        )
    )

    medium_min = (
        technical_rules.get("stability_medium_min")
    )

    high_min = (
        technical_rules.get("stability_high_min")
    )

    if medium_min is None or high_min is None:
        return (
            "PENDING_TECHNICAL_RULE",
            "stability_thresholds_not_defined"
        )

    medium_min = float(medium_min)
    high_min = float(high_min)

    if not (
        0 <= medium_min
        < high_min
        <= 100
    ):
        raise ValueError(
            "Os limites de stability devem respeitar: 0 <= medium < high <= 100."
        )

    value = float(row["stability_rate"])

    if value >= high_min:
        return (
            "HIGH",
            "high_stability"
        )

    if value >= medium_min:
        return (
            "MEDIUM",
            "medium_stability"
        )

    return (
        "LOW",
        "low_stability"
    )

#Status geral da classificação
def determine_classification_status(row):
    materiality_pending = (
        str(row["materiality_level"])
        .startswith("PENDING")
    )

    anomaly_pending = (
        row["anomaly_signal_level"] == "PENDING_TECHNICAL_RULE"
    )

    stability_pending = (
        row["stability_level"] == "PENDING_TECHNICAL_RULE"
    )

    technical_pending = (anomaly_pending or stability_pending)

    if materiality_pending and stability_pending:
        return "PENDING_BUSINESS_AND_TECHNICAL_RULES"

    if materiality_pending:
        return "PENDING_BUSINESS_RULE"

    if technical_pending:
        return "PENDING_TECHNICAL_RULE"

    review_status = (
        str(row["review_status"])
        .strip()
        .lower()
    )

    business_validation = (row["business_validation"])

    if (
        review_status != "validated"
        or pd.isna(business_validation)
    ):
        return "PENDING_BUSINESS_VALIDATION"

    return "READY_FOR_FINAL_CLASSIFICATION"

#Contrução da classificação
def build_classification(dataset, rules):
    result = dataset.copy()

    materiality = result.apply(
        lambda row: classify_materiality(row, rules), axis=1
    )

    result[
        "materiality_level"
    ] = [
        item[0]
        for item in materiality
    ]

    result[
        "materiality_reason"
    ] = [
        item[1]
        for item in materiality
    ]

    anomaly_signal = result.apply(
        lambda row: classify_anomaly_signal(row, rules), axis=1
    )

    result[
        "anomaly_signal_level"
    ] = [
        item[0]
        for item in anomaly_signal
    ]

    result[
        "anomaly_singal_level"
    ] = [
        item[1]
        for item in anomaly_signal
    ]

    stability = result.apply(
        lambda row: classify_stability(row, rules), axis=1
    )

    result[
        "stability_level"
    ] = [
        item[0]
        for item in stability
    ]

    result[
        "stability_reason"
    ] = [
        item[1]
        for item in stability
    ]

    result[
        "classification_status"
    ] = result.apply(
        determine_classification_status,
        axis=1
    )

    return result

#Resumo
def print_summary(result):
    print("\nStatus da classificação: \n")

    summary = (
        result["classification_status"]
        .value_counts(dropna=False)
        .to_string()
    )

    print(
        result["materiality_level"]
        .value_counts(dropna=False)
        .to_string()
    )

    print("\nSinal de anomalia: \n")

    print(
        result["anomaly_signal_level"]
        .value_counts(dropna=False)
        .to_string()
    )

    print("\nEstabilidade: \n")

    print(
        result["stability_level"]
        .value_counts(dropna=False)
        .to_string()
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
