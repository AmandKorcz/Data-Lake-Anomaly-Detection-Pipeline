#Diagnóstico exclusivamente offline para pré-homologação do KE24.

import ast
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_METADATA = {
    "_load_id",
    "_source_file_sha256",
    "_source_system",
    "_source_file",
    "_source_row_number",
    "_ingested_at_utc",
}
PROTECTED_TABLE = "KE24_staging_protected"

def _check(status, detail):
    return {"status": status, "detail": detail}

def _read_json(path):
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None

def _read_loader_tree(path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError, ValueError):
        return None

def _literal_assignment(tree, name):
    if tree is None:
        return None
    values = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets
            ):
                values.append(node.value)
    if len(values) != 1:
        return None
    try:
        value = ast.literal_eval(values[0])
    except (ValueError, TypeError, MemoryError, RecursionError):
        return None
    return value if isinstance(value, str) else None

def _writer_looks_disabled(tree):
    if tree is None:
        return False
    functions = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "load_to_bigquery"
    ]
    if len(functions) != 1 or len(functions[0].body) != 2:
        return False
    first, second = functions[0].body
    return (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Call)
        and isinstance(first.value.func, ast.Name)
        and first.value.func.id == "validate_bigquery_release"
        and isinstance(second, ast.Raise)
        and isinstance(second.exc, ast.Call)
        and isinstance(second.exc.func, ast.Name)
        and second.exc.func.id == "RuntimeError"
        and len(second.exc.args) == 1
        and isinstance(second.exc.args[0], ast.Constant)
        and isinstance(second.exc.args[0].value, str)
        and "Gravação BigQuery desabilitada" in second.exc.args[0].value
    )

def _schema_valid(policy, schema):
    if not isinstance(policy, dict) or not isinstance(schema, dict):
        return False
    columns = schema.get("columns")
    if (
        not isinstance(columns, list)
        or len(columns) != 167
        or any(not isinstance(item, str) for item in columns)
        or len(set(columns)) != len(columns)
        or "sales_quantity" not in columns
        or policy.get("schema_version") != schema.get("schema_version")
    ):
        return False
    dimensions = columns[:columns.index("sales_quantity")]
    keep = policy.get("keep_dimensions")
    tokenize = policy.get("tokenize_dimensions")
    actions = policy.get("metadata_actions")
    if (
        not isinstance(keep, list)
        or not isinstance(tokenize, list)
        or not all(isinstance(value, str) for value in keep + tokenize)
        or len(set(keep + tokenize)) != len(keep + tokenize)
        or set(keep + tokenize) != set(dimensions)
        or not isinstance(actions, dict)
        or set(actions) != EXPECTED_METADATA
        or any(action not in ("keep", "tokenize") for action in actions.values())
        or actions.get("_source_file") != "tokenize"
        or actions.get("_source_file_sha256") != "tokenize"
        or policy.get("measure_policy") != {
            "start_column": "sales_quantity", "action": "keep"
        }
    ):
        return False
    return True

def build_readiness_report(project_root=None):
   
    root = PROJECT_ROOT if project_root is None else Path(project_root)
    policy = _read_json(root / "config" / "ke24_privacy_policy.json")
    schema = _read_json(root / "config" / "ke24_schema.json")
    loader_tree = _read_loader_tree(root / "src" / "loading" / "load_ke24_bigquery.py")

    if policy is None:
        policy_check = _check("BLOCKED", "Política ausente ou inválida.")
    elif policy.get("status") == "DRAFT_REVIEW_REQUIRED":
        policy_check = _check("BLOCKED", "Política em revisão corporativa.")
    else:
        policy_check = _check(
            "REVIEW_REQUIRED",
            "Status local não comprova aprovação corporativa formal.",
        )

    schema_check = _check(
        "PASS" if _schema_valid(policy, schema) else "BLOCKED",
        "Contrato estrutural offline compatível."
        if _schema_valid(policy, schema) else "Contrato estrutural ausente ou incompatível.",
    )

    table = _literal_assignment(loader_tree, "BQ_TABLE")
    protected = _literal_assignment(loader_tree, "PROTECTED_BQ_TABLE")
    if protected != PROTECTED_TABLE or table != PROTECTED_TABLE:
        target_check = _check("BLOCKED", "Destino ativo não é o destino protegido autorizado.")
    else:
        target_check = _check(
            "REVIEW_REQUIRED", "Destino configurado; ainda exige revisão independente."
        )

    guard_check = _check(
        "PASS" if _writer_looks_disabled(loader_tree) else "NOT_VERIFIED",
        "Escrita parece incondicionalmente bloqueada no código analisado, requer testes independentes."
        if _writer_looks_disabled(loader_tree)
        else "Bloqueio estático da escrita não foi confirmado.",
    )

    return {
        "report_version": 1,
        "mode": "OFFLINE_ONLY",
        "overall_status": "BLOCKED",
        "release_authorized": False,
        "checks": {
            "privacy_policy": policy_check,
            "schema_contract": schema_check,
            "protected_destination": target_check,
            "writer_guard_static": guard_check,
            "gcp_permissions": _check("NOT_VERIFIED", "Permissões do GCP não verificadas."),
            "cloud_integration": _check("NOT_VERIFIED", "Sem testes reais de infraestrutura."),
            "corporate_approval": _check("NOT_VERIFIED", "Aprovação formal não comprovada."),
        },
    }


def main():
    print(json.dumps(build_readiness_report(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
