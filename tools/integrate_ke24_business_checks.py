import argparse
import ast
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = Path("src/pipeline/run_ke24.py")
GUARD = Path("src/pipeline/ke24_preview_execution_guard.py")


def _once(source, old, new):
    if old not in source or source.count(old) != 1:
        raise RuntimeError("Codigo de origem divergente; integracao nao aplicada.")
    return source.replace(old, new, 1)


def _modify_run(source):
    marker = "run_business_checks=run_readonly_business_checks,"
    if marker in source:
        if ("business_summary = None" not in source
                or 'result.update(business_summary)' not in source):
            raise RuntimeError("Integracao parcial detectada; revisar manualmente.")
        return source

    source = _once(
        source,
        "    from src.ml.load_ke24_alert_rules import load_protected_alert_rules\n",
        "    from src.ml.load_ke24_alert_rules import load_protected_alert_rules\n"
        "    from src.pipeline.ke24_business_checks_bridge import run_readonly_business_checks\n",
    )
    source = _once(
        source,
        "        load_rules=load_protected_alert_rules,\n",
        "        load_rules=load_protected_alert_rules,\n"
        "        run_business_checks=run_readonly_business_checks,\n",
    )
    source = _once(
        source,
        "    with _timed_stage(\"Feature engineering em memoria\"):\n",
        "    # O adapter real executa os checks; os dublês dos testes antigos\n"
        "    # continuam independentes e nao precisam fornecer esse método.\n"
        "    business_summary = None\n"
        "    if hasattr(adapters, 'run_business_checks'):\n"
        "        with _timed_stage(\"Regras oficiais de negocio - somente leitura\"):\n"
        "            business_summary = adapters.run_business_checks(processed, secret_key)\n"
        "        from src.pipeline.ke24_business_checks_bridge import SAFE_BUSINESS_KEYS\n"
        "        if not isinstance(business_summary, dict) or (\n"
        "            set(business_summary) != set(SAFE_BUSINESS_KEYS) or\n"
        "            any(type(value) is not int or value < 0 for value in business_summary.values())\n"
        "        ):\n"
        "            raise ValueError(\"Resumo das regras de negocio invalido.\")\n\n"
        "    with _timed_stage(\"Feature engineering em memoria\"):\n",
    )
    source = _once(
        source,
        "    if classification_summary is not None:\n        result.update(classification_summary)\n",
        "    if business_summary is not None:\n"
        "        result.update(business_summary)\n"
        "        LOG.info(\n"
        "            \"REGRAS OFICIAIS: total=%d, pass=%d, revisao_media=%d, \"\n"
        "            \"revisao_alta=%d, violacao=%d, pendentes=%d\",\n"
        "            result['business_checks_total'], result['business_checks_pass'],\n"
        "            result['business_checks_review_medium'], result['business_checks_review_high'],\n"
        "            result['business_checks_violation'], result['business_checks_pending'],\n"
        "        )\n"
        "        LOG.info(\n"
        "            \"PENDENTES: historico=%d, base_zero=%d, moeda_nao_configurada=%d; \"\n"
        "            \"receita=%d, materiais=%d\",\n"
        "            result['business_pending_history'], result['business_pending_zero_baseline'],\n"
        "            result['business_pending_company_currency'],\n"
        "            result['business_pending_revenue'], result['business_pending_material'],\n"
        "        )\n"
        "    if classification_summary is not None:\n        result.update(classification_summary)\n",
    )
    source = _once(
        source,
        '                   "priority_low"} and type(value) is int:\n',
        '                   "priority_low", "business_checks_total", "business_checks_pass",\n'
        '                   "business_checks_review_medium", "business_checks_review_high",\n'
        '                   "business_checks_violation", "business_checks_pending",\n'
        '                   "business_pending_history", "business_pending_zero_baseline",\n'
        '                   "business_pending_company_currency", "business_pending_revenue",\n'
        '                   "business_pending_material"} and type(value) is int:\n',
    )
    return source


def _modify_guard(source):
    if '    "business_checks_total",' in source:
        if '    "business_pending_material",' not in source:
            raise RuntimeError("Whitelist parcial de metricas de negocio.")
        return source
    return _once(
        source,
        '    "priority_low",\n)\n',
        '    "priority_low",\n'
        '    "business_checks_total",\n'
        '    "business_checks_pass",\n'
        '    "business_checks_review_medium",\n'
        '    "business_checks_review_high",\n'
        '    "business_checks_violation",\n'
        '    "business_checks_pending",\n'
        '    "business_pending_history",\n'
        '    "business_pending_zero_baseline",\n'
        '    "business_pending_company_currency",\n'
        '    "business_pending_revenue",\n'
        '    "business_pending_material",\n)\n',
    )


def plan(root=ROOT):
    mods = {}
    for path, updater in [(RUN, _modify_run), (GUARD, _modify_guard)]:
        source = (root / path).read_text(encoding="utf-8")
        new = updater(source)
        ast.parse(new, filename=str(path))
        mods[path] = (source, new)
    return mods


def apply(root=ROOT):
    mods = plan(root)
    for path, (previous, updated) in mods.items():
        if previous == updated:
            continue
        target = root / path
        descriptor, temp = tempfile.mkstemp(prefix=".ke24_", suffix=".py", dir=target.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(updated)
            os.replace(temp, target)
        finally:
            Path(temp).unlink(missing_ok=True)
    return [str(path) for path, (old, new) in mods.items() if old != new]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Integra checks oficiais no run_ke24.py")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Somente validar compatibilidade, sem escrita")
    mode.add_argument("--apply", action="store_true", help="Modificar os dois modulos Python")
    args = parser.parse_args(argv)
    if args.check:
        mods = plan()
        print("Compatibilidade: APROVADA | arquivos com mudancas:", sum(a != b for a, b in mods.values()))
    else:
        changed = apply()
        print("Integracao aplicada | arquivos modificados:", len(changed))
    print("Nenhuma conexao ou publicacao realizada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
