import hashlib
import json
import re
from datetime import datetime, timezone

import pandas as pd

REQUIRED_COLUMNS = [
    "_source_file",
    "_source_file_sha256",
    "_source_row_number",
    "company_code",
    "reference_document",
    "period_key",
    "alert_level",
    "classification_status"
]

TOKEN_PATTERN = r"TKN_[0-9a-f]{32}"

ALLOWED_ALERT_LEVELS = {
    "LOW",
    "MEDIUM",
    "HIGH",
    "PENDING"
}

ALLOWED_CLASSIFICATION_STATUS = {
    "EXPERIMENTAL_CLASSIFICATION",
    "PENDING_BUSINESS_RULE",
    "PENDING_TECHNICAL_RULE",
    "PENDING_BUSINESS_VALIDATION",
    "READY_FOR_FINAL_CLASSIFICATION"
}

def validate_alert_input(dataset):
    if not isinstance(dataset, pd.DataFrame):
        raise ValueError("A entrada deve ser um DataFrame")

    if dataset.empty:
        raise ValueError("O dataset de alertas está vazio")

    missing = [
        column
        for column in REQUIRED_COLUMNS
        if column not in dataset.columns
    ]

    if missing:
        raise ValueError(
            "Colunas obrigatórias ausentes:"
            +", ".join(missing)
        )

    if dataset.columns.duplicated().any():
        raise ValueError("Existem colunas duplicadas no dataset.")

    #Confere os identificadores obriatoriamente protegidos
    for column in (
        "_source_file",
        "_source_file_sha256",
        "company_code"
    ):
        values = dataset[column].astype("string").str.strip()

        valid = (
            values.str.fullmatch(TOKEN_PATTERN).fillna(False)
        )

        if not valid.all():
            raise ValueError(
                f"Existem identificadores não protegidos em {column}."
            )

    #Documento pode estar ausente, mas não em seu texto original
    documents = (
        dataset["reference_document"]
        .astype("string")
        .str.strip()
    )

    valid_documents = (
        documents.isna() | documents.str.fullmatch(TOKEN_PATTERN).fillna(False)
    )

    if not valid_documents.all():
        raise ValueError("Existem documentos de referência não protegidos")

    #Valida a linha de origem
    row_numbers = pd.to_numeric(
        dataset["_source_row_number"],
        errors="coerce"
    )

    valid_rows = (
        row_numbers.notna()
        & row_numbers.gt(0)
        & row_numbers.mod(1).eq(0)
    )

    if not valid_rows.fillna(False).all():
        raise ValueError("Existem números de linha de origem inválida.")

    # Valida o período YYYY-MM
    periods = (
        dataset["period_key"]
        .astype("string")
        .str.strip()
    )

    valid_periods = (
        periods.str.fullmatch(
            r"\d{4}-(0[1-9]|1[0-2])"
        ).fillna(False)
    )

    if not valid_periods.all():
        raise ValueError("Existem períodos inválidos.")

     # Confere os níveis de alerta
    levels = (
        dataset["alert_level"]
        .astype("string")
        .str.strip()
    )

    if not levels.isin(ALLOWED_ALERT_LEVELS).all():
        raise ValueError(
            "Existem níveis de alerta inválidos."
        )

    # Confere os status de classificação
    statuses = (
        dataset["classification_status"]
        .astype("string")
        .str.strip()
    )

    if not statuses.isin(
        ALLOWED_CLASSIFICATION_STATUS
    ).all():
        raise ValueError(
            "Existem status de classificação inválidos."
        )

    # Um registro de origem não pode aparecer duas vezes
    identity_columns = [
        "_source_file_sha256",
        "_source_row_number",
        "company_code",
    ]

    if dataset.duplicated(
        subset=identity_columns
    ).any():
        raise ValueError(
            "Existem registros de origem duplicados."
        )

def generate_alert_id(source_file_sha256, source_row_number, company_code):

    # Utiliza somente identificadores já protegidos
    for value in (source_file_sha256, company_code):
        if not isinstance(value, str) or not re.fullmatch(
            TOKEN_PATTERN, value
        ):
            raise ValueError(
                "Não é permitido gerar IDs com identificadores originais."
            )

    if (
        isinstance(source_row_number, bool)
        or not isinstance(source_row_number, int)
        or source_row_number <= 0
    ):
        raise ValueError("Número de linha inválido.")

    payload = json.dumps(
        [
            "KE24_ALERT_ID_V1",
            source_file_sha256,
            source_row_number,
            company_code,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )

    digest = hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()

    return f"ALT_{digest[:32]}"

def build_alert_records(dataset, detected_at_utc):

    validate_alert_input(dataset)

    if (
        not isinstance(detected_at_utc, datetime)
        or detected_at_utc.tzinfo is None
        or detected_at_utc.utcoffset() is None
    ):
        raise ValueError(
            "A data de detecção deve possuir timezone."
        )

    timestamp = (
        detected_at_utc
        .astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    result = dataset[REQUIRED_COLUMNS].copy()
    result = result.reset_index(drop=True)

    # Normaliza os dados necessários
    for column in (
        "_source_file",
        "_source_file_sha256",
        "company_code",
        "period_key",
        "alert_level",
        "classification_status",
    ):
        result[column] = (
            result[column].astype("string").str.strip()
        )

    result["_source_row_number"] = (
        pd.to_numeric(
            result["_source_row_number"]
        ).astype("int64")
    )

    # Preserva documentos ausentes como None
    result["reference_document"] = [
        None if pd.isna(value) else str(value).strip()
        for value in result["reference_document"]
    ]

    # Identificador estável, independente do nível de alerta
    result["alert_id"] = [
        generate_alert_id(
            source_file_sha256,
            int(row_number),
            company_code,
        )
        for source_file_sha256, row_number, company_code in zip(
            result["_source_file_sha256"],
            result["_source_row_number"],
            result["company_code"],
        )
    ]

    if result["alert_id"].duplicated().any():
        raise ValueError(
            "Foram gerados identificadores de alerta duplicados."
        )

    result["investigation_status"] = "DETECTED"
    result["detected_at_utc"] = timestamp
    result["updated_at_utc"] = timestamp

    output_columns = [
        "alert_id",
        "company_code",
        "reference_document",
        "period_key",
        "alert_level",
        "classification_status",
        "investigation_status",
        "detected_at_utc",
        "updated_at_utc",
        "_source_file",
        "_source_file_sha256",
        "_source_row_number",
    ]

    return result[output_columns].copy()