
import json
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

POLICY_FILE = PROJECT_ROOT / "config" / "ke24_privacy_policy.json"
SCHEMA_FILE = PROJECT_ROOT / "config" / "ke24_schema.json"


def load_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def load_privacy_runtime():
    policy = load_json(POLICY_FILE)
    schema = load_json(SCHEMA_FILE)

    # Bloqueia a execução enquanto a política não for aprovada
    if policy.get("status") != "APPROVED":
        raise RuntimeError(
            "Processamento bloqueado: a política de privacidade "
            "ainda precisa de revisão e aprovação corporativa. "
            f"Status atual: {policy.get('status')}"
        )

    # Verifica a compatibilidade com o contrato KE24
    if policy["schema_version"] != schema["schema_version"]:
        raise ValueError("Versão do schema incompatível.")

    columns = schema["columns"]
    dimensions = columns[:columns.index("sales_quantity")]

    classified = (
        policy["keep_dimensions"]
        + policy["tokenize_dimensions"]
    )

    if (
        len(classified) != len(set(classified))
        or set(classified) != set(dimensions)
    ):
        raise ValueError(
            "A política não classifica corretamente todas as dimensões."
        )

    expected_metadata = {
        "_load_id",
        "_source_file_sha256",
        "_source_system",
        "_source_file",
        "_source_row_number",
        "_ingested_at_utc",
    }

    actions = policy["metadata_actions"]

    if (
        set(actions) != expected_metadata
        or any(
            value not in {"keep", "tokenize"}
            for value in actions.values()
        )
    ):
        raise ValueError("Política de metadados inválida.")

    if policy["measure_policy"] != {
        "start_column": "sales_quantity",
        "action": "keep",
    }:
        raise ValueError("Política de medidas incompatível.")

    # Chave armazenada fora do repositório
    key_file = os.environ.get("KE24_HMAC_KEY_FILE")

    if not key_file:
        raise RuntimeError(
            "Local da chave HMAC não configurado."
        )

    key_path = Path(key_file)

    if not key_path.is_file():
        raise FileNotFoundError(
            "Arquivo da chave HMAC não encontrado."
        )

    try:
        secret_key = bytes.fromhex(
            key_path.read_text(encoding="ascii").strip()
        )
    except ValueError as error:
        raise ValueError("Formato da chave HMAC inválido.") from error

    if len(secret_key) != 32:
        raise ValueError("A chave HMAC deve possuir 32 bytes.")

    return policy, schema, secret_key
