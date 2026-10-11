from pathlib import Path
import json

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SCHEMA_FILE = PROJECT_ROOT / "config" / "ke24_schema.json"
POLICY_FILE = PROJECT_ROOT / "config" / "ke24_privacy_policy.json"

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


def main():
    schema = load_json(SCHEMA_FILE)
    policy = load_json(POLICY_FILE)

    if policy["schema_version"] != schema["schema_version"]:
        raise ValueError("Versão do schema incompatível.")

    columns = schema["columns"]

    if len(columns) != schema["expected_column_count"]:
        raise ValueError("Contrato KE24 inconsistente.")

    if len(set(columns)) != len(columns):
        raise ValueError("Colunas duplicadas no schema.")

    measure_start = policy["measure_policy"]["start_column"]

    if measure_start != "sales_quantity":
        raise ValueError("Início das medidas diferente do contrato.")

    if measure_start not in columns:
        raise ValueError("Coluna inicial das medidas não encontrada.")

    start_index = columns.index(measure_start)

    dimensions = set(columns[:start_index])
    measures = columns[start_index:]

    keep = policy["keep_dimensions"]
    tokenize = policy["tokenize_dimensions"]

    configured = keep + tokenize

    if len(configured) != len(set(configured)):
        raise ValueError("Existem colunas duplicadas na política.")

    missing = dimensions - set(configured)
    unexpected = set(configured) - dimensions

    if missing or unexpected:
        raise ValueError(
            "Classificação incompleta ou incorreta.\n"
            f"Ausentes: {sorted(missing)}\n"
            f"Inesperadas: {sorted(unexpected)}"
        )

    metadata = policy["metadata_actions"]

    if set(metadata) != EXPECTED_METADATA:
        raise ValueError(
            "Os metadados não correspondem ao contrato."
        )

    if any(
        action not in {"keep", "tokenize"}
        for action in metadata.values()
    ):
        raise ValueError("Tratamento de metadados inválido.")

    if policy["measure_policy"]["action"] != "keep":
        raise ValueError("Política de medidas não reconhecida.")

    total = len(configured) + len(measures) + len(metadata)

    print("\nKE24 - Validação da Política de Privacidade")
    print(f"Versão: {policy['policy_version']}")
    print(f"Dimensões preservadas: {len(keep)}")
    print(f"Dimensões para tokenização: {len(tokenize)}")
    print(f"Medidas financeiras: {len(measures)}")
    print(f"Metadados: {len(metadata)}")
    print(f"Total de campos classificados: {total}")

    print("\nCobertura do contrato: APROVADA")
    print(f"Status da política: {policy['status']}")

    if policy["status"] != "DRAFT_REVIEW_REQUIRED":
        print("Atenção: verificar aprovação da política.")


if __name__ == "__main__":
    main()
