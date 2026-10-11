from copy import deepcopy

from src.privacy.anonymize_ke24 import generate_token

def protect_company_rule_keys(rules, secret_key):

    # Valida a configuração recebida
    if not isinstance(rules, dict):
        raise ValueError(
            "A configuração das regras deve ser um dicionário."
        )

    if not isinstance(secret_key, bytes) or len(secret_key) < 32:
        raise ValueError(
            "A chave HMAC deve possuir pelo menos 32 bytes."
        )

    company_rules = rules.get("business_thresholds_by_company")

    if not isinstance(company_rules, dict) or not company_rules:
        raise ValueError(
            "As regras financeiras por empresa são inválidas."
        )

    # Preserva a configuração original
    protected_rules = deepcopy(rules)
    protected_companies = {}

    for company_code, thresholds in company_rules.items():
        # Valida o identificador
        if not isinstance(company_code, str) or not company_code.strip():
            raise ValueError(
                "Identificador de empresa inválido."
            )

        if not isinstance(thresholds, dict):
            raise ValueError(
                "Configuração financeira inválida."
            )

        # Utiliza o mesmo HMAC aplicado na ingestão
        company_token = generate_token(
            value=company_code,
            column="company_code",
            company=company_code,
            secret_key=secret_key,
        )

        # Impede duplicidade após normalização
        if company_token in protected_companies:
            raise ValueError(
                "Foram identificadas regras duplicadas "
                "após a tokenização."
            )

        protected_companies[company_token] = deepcopy(thresholds)

    # Substitui as chaves pelos identificadores protegidos
    protected_rules[
        "business_thresholds_by_company"
    ] = protected_companies

    return protected_rules