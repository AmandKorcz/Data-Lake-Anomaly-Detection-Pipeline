#Checklist de evidências KE24

from src.loading.ke24_readiness_report import build_readiness_report

# A lista define evidências exigidas
CONTROLS = (
    (
        "privacy_policy", "Revisão da política de privacidade",
        "Governança de dados", "Parecer formal sobre dados e regras de tokenização",
        "privacy_policy", "review",
    ),
    (
        "schema_contract", "Contrato e classificação do schema KE24",
        "Engenharia de dados", "Testes e revisão do contrato de campos",
        "schema_contract", "offline",
    ),
    (
        "protected_destination", "Destino exclusivamente protegido",
        "Engenharia de dados", "Configuração revisada do destino isolado",
        "protected_destination", "review",
    ),
    (
        "write_disabled", "Bloqueio de escrita no carregador",
        "Engenharia de software", "Revisão de código e testes do bloqueio de gravação",
        "writer_guard_static", "offline",
    ),
    (
        "iam_least_privilege", "Permissões mínimas no GCP",
        "Segurança da informação", "Revisão de permissões e segregação de contas e ambientes",
        None, "external",
    ),
    (
        "gcs_metadata_controls", "Proteção do lock e do diário no GCS",
        "Engenharia de dados", "Verificação autorizada de gerações, IAM e retenção",
        None, "external",
    ),
    (
        "bigquery_job_identity", "Identidade e região do job BigQuery",
        "Engenharia de dados", "Teste autorizado do identificador determinístico e da região",
        None, "external",
    ),
    (
        "exact_batch_evidence", "Manifesto e lote exato da tentativa",
        "Engenharia de dados", "Conferência independente de hashes protegidos, meses e quantidades",
        None, "external",
    ),
    (
        "hmac_key_custody", "Custódia da chave HMAC",
        "Segurança da informação", "Revisão de acesso, rotação e armazenamento; nunca anexar a chave",
        None, "external",
    ),
    (
        "synthetic_cloud_trial", "Validação com dados sintéticos em ambiente isolado",
        "Engenharia de dados", "Evidências aprovadas de execução controlada sem dados corporativos",
        None, "external",
    ),
    (
        "failure_recovery", "Recuperação e controle de concorrência",
        "Engenharia de software", "Plano de recuperação e ensaios autorizados de falha e reconciliação",
        None, "external",
    ),
    (
        "corporate_approval", "Aprovação formal para uso operacional",
        "Governança de dados", "Aceite formal dos responsáveis e análise dos riscos residuais",
        None, "external",
    ),
)

VALID_SOURCE_STATUSES = {"PASS", "BLOCKED", "NOT_VERIFIED", "REVIEW_REQUIRED"}

def _safe_status(readiness, key):
    if not isinstance(readiness, dict):
        return "NOT_VERIFIED"
    checks = readiness.get("checks")
    if not isinstance(checks, dict):
        return "NOT_VERIFIED"
    result = checks.get(key)
    if not isinstance(result, dict):
        return "NOT_VERIFIED"
    value = result.get("status")
    return value if isinstance(value, str) and value in VALID_SOURCE_STATUSES else "NOT_VERIFIED"

def _control_status(source_status, kind):
    if kind == "external":
        return "NOT_VERIFIED"
    if source_status == "BLOCKED":
        return "BLOCKED"
    if kind == "offline" and source_status == "PASS":
        return "OFFLINE_PASS"
    # REVIEW_REQUIRED, PASS para controle formal e estados ausentes nunca se transformam em comprovação de aprovação.
    return "NOT_VERIFIED"

def build_homologation_checklist(project_root=None):
    try:
        readiness = build_readiness_report(project_root)
    except Exception:
        readiness = None  
        # Falha fechada: nenhuma evidência será considerada verificada.

    items = []
    for code, label, owner, evidence, source, kind in CONTROLS:
        source_status = _safe_status(readiness, source) if source else "NOT_VERIFIED"
        items.append({
            "id": code,
            "control": label,
            "responsible_role": owner,
            "required_evidence": evidence,
            "status": _control_status(source_status, kind),
        })

    return {
        "checklist_version": 1,
        "mode": "OFFLINE_ONLY",
        "overall_status": "BLOCKED",
        "release_authorized": False,
        "items": items,
        "summary": {
            "offline_pass": sum(item["status"] == "OFFLINE_PASS" for item in items),
            "blocked": sum(item["status"] == "BLOCKED" for item in items),
            "not_verified": sum(item["status"] == "NOT_VERIFIED" for item in items),
        },
    }


def render_checklist_markdown(checklist):
    if (
        not isinstance(checklist, dict)
        or checklist.get("mode") != "OFFLINE_ONLY"
        or checklist.get("overall_status") != "BLOCKED"
        or checklist.get("release_authorized") is not False
        or not isinstance(checklist.get("items"), list)
    ):
        raise ValueError("Checklist inválido ou não bloqueado.")

    lines = [
        "# KE24 - Checklist de homologação (offline)",
        "",
        "**Resultado: BLOCKED — nenhuma liberação operacional autorizada.**",
        "",
        "Aprovações e evidências externas devem ser verificadas por responsáveis autorizados.",
        "Os itens OFFLINE_PASS confirmam apenas análises locais, não testes reais no GCP.",
        "",
        "| Controle | Status | Responsável | Evidência necessária |",
        "|---|---|---|---|",
    ]
    if [item.get("id") if isinstance(item, dict) else None for item in checklist["items"]] != [entry[0] for entry in CONTROLS]:
        raise ValueError("Itens de checklist inválidos.")
    for item in checklist["items"]:
        # Reconstrói o conteúdo a partir do catálogo: nunca reflete textos arbitrários.
        definition = next((entry for entry in CONTROLS if entry[0] == item.get("id")), None)
        if definition is None or item.get("status") not in {"BLOCKED", "OFFLINE_PASS", "NOT_VERIFIED"}:
            raise ValueError("Item de checklist inválido.")
        code, label, owner, evidence, _source, kind = definition
        if kind == "external" and item["status"] != "NOT_VERIFIED":
            raise ValueError("Evidência externa não confirmada.")
        if kind == "review" and item["status"] == "OFFLINE_PASS":
            raise ValueError("Aprovação formal não comprovada.")
        lines.append(f"| {label} | {item['status']} | {owner} | {evidence} |")

    lines.extend([
        "",
        "Observação: não anexar chaves, dados financeiros, identificadores originais ou logs sensíveis.",
        "O checklist não substitui aceite corporativo, validação de IAM ou homologação em nuvem.",
    ])
    return "\n".join(lines) + "\n"


def main():
    print(render_checklist_markdown(build_homologation_checklist()), end="")


if __name__ == "__main__":
    main()
