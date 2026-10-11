
import hashlib
import hmac
import json

import pandas as pd


# Campos que compartilham o mesmo tipo de identificador
TOKEN_DOMAINS = {
    "customer": "business_partner",
    "ship_to_party": "business_partner",
}

# Campos que não dependem da empresa para gerar o token
GLOBAL_FIELDS = {
    "company_code",
    "_source_file",
    "_source_file_sha256",
}


def normalize_value(value):
    #Prepara o valor para tokenização sem perder zeros à esquerda.

    if pd.isna(value):
        return None

    normalized = str(value).strip()

    if not normalized:
        return None

    return normalized


def generate_token(value, column, company, secret_key):
    #Gera um token determinístico usando HMAC-SHA256.

    normalized_value = normalize_value(value)

    if normalized_value is None:
        return pd.NA

    # Define um domínio para evitar misturar tipos de códigos
    domain = TOKEN_DOMAINS.get(column, column)

    if column in GLOBAL_FIELDS:
        company_scope = None
    else:
        company_scope = normalize_value(company)

        if company_scope is None:
            raise ValueError(
                f"Empresa de origem ausente para o campo '{column}'."
            )

    payload = json.dumps(
        ["KE24_PRIVACY_V1", domain, company_scope, normalized_value],
        ensure_ascii=False,
        separators=(",", ":"),
    )

    digest = hmac.new(
        secret_key,
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return f"TKN_{digest[:32]}"


def anonymize_ke24_dataframe(df, policy, schema, secret_key):
    #Aplica a política de tokenização ao DataFrame

    if not isinstance(secret_key, bytes) or len(secret_key) < 32:
        raise ValueError(
            "A chave HMAC deve ter pelo menos 32 bytes."
        )

    if policy["schema_version"] != schema["schema_version"]:
        raise ValueError("Versões de schema incompatíveis.")

    if df.columns.duplicated().any():
        raise ValueError("Existem colunas duplicadas.")

    expected_columns = (
        set(schema["columns"])
        | set(policy["metadata_actions"])
    )

    received_columns = set(df.columns)

    missing = expected_columns - received_columns
    unexpected = received_columns - expected_columns

    if missing or unexpected:
        raise ValueError(
            "Layout incompatível com a política de privacidade.\n"
            f"Ausentes: {sorted(missing)}\n"
            f"Inesperadas: {sorted(unexpected)}"
        )

    # Inclui dimensões e metadados marcados para tokenização
    token_columns = list(policy["tokenize_dimensions"])

    token_columns.extend(
        column
        for column, action in policy["metadata_actions"].items()
        if action == "tokenize"
    )

    if len(token_columns) != len(set(token_columns)):
        raise ValueError("Campos de tokenização duplicados.")

    # Guarda os códigos originais para definir o escopo
    original_companies = df["company_code"]

    protected_df = df.copy()

    for column in token_columns:
        protected_values = [
            generate_token(
                value=value,
                column=column,
                company=company,
                secret_key=secret_key,
            )
            for value, company in zip(
                df[column],
                original_companies,
            )
        ]

        protected_df[column] = pd.array(
            protected_values,
            dtype="string",
        )

    if len(protected_df) != len(df):
        raise ValueError(
            "A quantidade de registros foi alterada."
        )

    if list(protected_df.columns) != list(df.columns):
        raise ValueError(
            "A estrutura de colunas foi alterada."
        )

    return protected_df
