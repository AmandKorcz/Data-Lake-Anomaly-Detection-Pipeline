from pathlib import Path 
import json

import pandas as pd

#Configurações
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

#Carregamento
def load_rules():
    if not RULES_FILE.exists():
        raise FileNotFoundError(
            f"Arquivo de regras não encontrado: {RULES_FILE}"
        )

    with open(
        RULES_FILE,
        "r",
        encoding="utf-8"
    ) as file:
        return json.load(file)

#Validação geral
def validate_dataset(dataset):
    required_columns = [
        "company_code",
        "currency_type",
        "currency"
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

#Validação das empresas
def validate_company_rules(dataset, rules):
    companies = (
        dataset[
            [
                "company_code",
                "currency_type",
                "currency"
            ]
        ]
        .drop_duplicates()
        .sort_values("company_code")
    )

    configured_companies = (
        rules.get(
            "business_thresholds_by_company",
            {}
        )
    )

    rows = []

    for _, company in companies.iterrows():

        company_code = (
            str(company["company_code"])
            .strip()
            .upper()
        )
        
        currency = (
            str(company["currency"])
            .strip()
            .upper()
        )
        
        currency_type = int(company["currency_type"])

        company_rules = (
            configured_companies.get(company_code)
        )

        if company_rules is None:
            rows.append({
                "company_code": company_code,
                "currency_type": currency_type,
                "currency": currency,
                "configuration_status": "MISSING_COMPANY_RULE",
                "currency_status": "NOT_VALIDATED",
                "materiality_status": "NOT_CONFIGURED"
            })

            continue

        configured_currency = (
            str(
                company_rules.get("currency", "")
            )
            .strip()
            .upper()
        )

        if configured_currency == currency:
            currency_status = "OK"
        else: 
            currency_status = ("CURRENCY_MISMATCH")

        medium_min = (
            company_rules.get("materiality_medium_min")
        )

        high_min = (
            company_rules.get("materiality_high_min")
        )

        if medium_min is None or high_min is None:
            materiality_status = ("PENDING_BUSINESS_RULE")
        elif high_min <= medium_min:
            materiality_status = ("INVALID_THRESHOLDS")
        else:
            materiality_status = "READY"

        rows.append({
            "company_code": company_code,
            "currency_type": currency_type,
            "currency": currency,
            "configuration_status": "CONFIGURED",
            "currency_status": currency_status,
            "materiality_status": materiality_status
        })

    return pd.DataFrame(rows)

#Validação dos limites técnicos
def validate_technical_rules (rules):
    technical_rules = (
        rules.get(
            "technical_thresholds",
            {}
        )
    )

    required_thresholds = [
        "anomaly_percentile_medium_min",
        "anomaly_percentile_high_min",
        "stability_medium_min",
        "stability_high_min"
    ]

    missing_values = [
        threshold
        for threshold in required_thresholds
        if technical_rules.get(threshold) is None
    ]

    if missing_values:
        return (
            "PENDING_TECHNICAL_RULE",
            missing_values
        )

    anomaly_medium = (
        technical_rules["anomaly_percentile_medium_min"]
    )

    anomaly_high = (
        technical_rules["anomaly_percentile_high_min"]
    )

    stability_medium = (
        technical_rules["stability_medium_min"]
    )

    stability_high = (
        technical_rules["stability_high_min"]
    )

    if anomaly_high <= anomaly_medium:
        raise ValueError("anomaly_percentile_high_min deve ser maior que anomaly_percentile_medium_min")

    if stability_high <= stability_medium:
        raise ValueError("stability_high_min deve ser maior que stability_medium_min")

    return (
        "READY",
        []
    )

#Pipeline principal
def main():
    print("\nKE24 - Validação das Regras de Alerta\n")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Dataset de validação não encontrado {INPUT_FILE}"
        )

    dataset = pd.read_csv(
        INPUT_FILE,
        encoding="utf-8-sig"
    )

    rules = load_rules()

    validate_dataset(dataset)

    expected_currency_type = (
        rules ["currency_basis"]["currency_type"]
    )

    invalid_currency_type = (
        dataset["currency_type"]
        .ne(expected_currency_type)
    )

    if invalid_currency_type.any():
        raise ValueError("Existem registros fora da bse de moeda configurada")

    company_validation = (
        validate_company_rules(dataset, rules)
    )
    (
        technical_status,
        pending_technical_rules
    ) = validate_technical_rules(rules)

    print(f"Empresas encontradas: {len(company_validation):,}")

    print("\nStatus por empresa: \n")

    print(company_validation.to_string(index=False))

    print("\nStatus das regras técnicas: ")
    print(technical_status)

    if pending_technical_rules:
        print("\nLimites técnicos ainda não definidos: ")

        for rule in pending_technical_rules:
            print(f"- {rule}")

    ready_companies = (
        company_validation["materiality_status"]
        .eq("READY")
        .sum()
    )

    total_companies = len(company_validation)

    print("\nResumo: ")
    print(f"Empresas prontas para classificação financeira: {ready_companies}/{total_companies}")

if __name__ == "__main__":
    main()