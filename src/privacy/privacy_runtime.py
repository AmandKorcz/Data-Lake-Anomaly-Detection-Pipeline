
import json
from pathlib import Path

from src.privacy.hmac_key import load_hmac_key_from_environment


PROJECT_ROOT = Path(__file__).resolve().parents[2]

POLICY_FILE = PROJECT_ROOT / "config" / "ke24_privacy_policy.json"
SCHEMA_FILE = PROJECT_ROOT / "config" / "ke24_schema.json"

EXPECTED_METADATA = {
    "_load_id",
    "_source_file_sha256",
    "_source_system",
    "_source_file",
    "_source_row_number",
    "_ingested_at_utc",
}


def load_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def load_privacy_runtime():
    policy = load_json(POLICY_FILE)
    schema = load_json(SCHEMA_FILE)

    # A política precisa ter aprovação corporativa
    if policy.get("status") != "APPROVED":
        raise RuntimeError(
            "Processamento bloqueado: a política de privacidade "
            "ainda precisa de revisão e aprovação corporativa. "
            f"Status atual: {policy.get('status')}"
        )

    # Confere a versão do schema
    if policy["schema_version"] != schema["schema_version"]:
        raise ValueError("Versão do schema incompatível.")

    columns = schema["columns"]

    if "sales_quantity" not in columns:
        raise ValueError("Coluna sales_quantity não encontrada.")

    dimensions = columns[
        :columns.index("sales_quantity")
    ]

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

    # Verifica o contrato dos metadados
    actions = policy["metadata_actions"]

    if (
        set(actions) != EXPECTED_METADATA
        or any(
            action not in {"keep", "tokenize"}
            for action in actions.values()
        )
    ):
        raise ValueError("Política de metadados inválida.")

    # As medidas financeiras devem permanecer preservadas
    if policy["measure_policy"] != {
        "start_column": "sales_quantity",
        "action": "keep",
    }:
        raise ValueError("Política de medidas incompatível.")

    # Carrega a chave somente depois das validações
    secret_key = load_hmac_key_from_environment()

    return policy, schema, secret_key
