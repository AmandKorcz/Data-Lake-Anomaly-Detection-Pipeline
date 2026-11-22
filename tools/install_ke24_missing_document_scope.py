from __future__ import annotations
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECURRENCE = ROOT / "src" / "review" / "ke24_review_recurrence.py"
PERSISTENCE = ROOT / "src" / "pipeline" / "run_ke24_drive_alert_persistence.py"
PATCHES = [
    (
        '    unmatchable_candidates: int\n\n    @property\n    def publication_blockers(self) -> int:\n        return self.ambiguous_candidates + self.unmatchable_candidates',
        '    unmatchable_candidates: int\n    safe_unmatchable_candidates: int = 0\n\n    @property\n    def publication_blockers(self) -> int:\n        return (self.ambiguous_candidates + self.unmatchable_candidates\n                - self.safe_unmatchable_candidates)',
    ),
    (
        'def _business_key(row):',
        'def _business_key(row, *, allow_missing_document=False):',
    ),
    (
        '    if document is None:\n        return None\n    period = str(row["period_key"]).strip()',
        '    if document is None and not allow_missing_document:\n        return None\n    period = str(row["period_key"]).strip()',
    ),
    (
        '    old_keys = defaultdict(list)\n    unknown_old_scopes = set()\n    current_keys = defaultdict(list)',
        '    old_keys = defaultdict(list)\n    unknown_old_scopes = set()\n    current_keys = defaultdict(list)\n    current_partial_keys = defaultdict(list)',
    ),
    (
        '    new_rows = current.to_dict(orient="records")\n    for row in old_rows:',
        '    new_rows = current.to_dict(orient="records")\n    # Escopo histórico é suficiente para excluir recorrência entre competências\n    # distintas; nunca é suficiente para suprimir lançamentos na mesma competência.\n    old_scopes = {(row["company_code"], row["period_key"]) for row in old_rows}\n    for row in old_rows:',
    ),
    (
        '    for row in new_rows:\n        key = _business_key(row)\n        if key is not None:\n            current_keys[key].append(row["alert_id"])',
        '    for row in new_rows:\n        key = _business_key(row)\n        if key is not None:\n            current_keys[key].append(row["alert_id"])\n        # Chave parcial só detecta colisões potenciais dentro do lote atual;\n        # ela NUNCA autoriza suprimir um alerta ou afirmar que é recorrente.\n        partial = _business_key(row, allow_missing_document=True)\n        if partial is not None:\n            current_partial_keys[(partial[0], *partial[2:])].append(row["alert_id"])',
    ),
    (
        '    exact = cross_export = treated = pending = ambiguous = unmatchable = 0',
        '    exact = cross_export = treated = pending = ambiguous = unmatchable = 0\n    safe_unmatchable = 0',
    ),
    (
        '            if key is None:\n                unmatchable += 1\n            elif (row["company_code"], row["period_key"]) in unknown_old_scopes:',
        '            if key is None:\n                unmatchable += 1\n                # A ausência de documento NÃO autoriza comparar por valores.\n                # Porém, sem histórico na mesma empresa e competência, não\n                # existe alerta anterior nesse escopo que possa ser recorrente.\n                # Ainda exigimos os demais campos e ausência de colisão parcial\n                # com outro candidato deste mesmo lote.\n                partial = _business_key(row, allow_missing_document=True)\n                if (partial is not None\n                        and _protected(row["reference_document"], required=False) is None\n                        and (row["company_code"], row["period_key"]) not in old_scopes\n                        and len(current_partial_keys[(partial[0], *partial[2:])]) == 1):\n                    safe_unmatchable += 1\n            elif (row["company_code"], row["period_key"]) in unknown_old_scopes:',
    ),
    (
        '        unmatchable_candidates=unmatchable,\n    )',
        '        unmatchable_candidates=unmatchable,\n        safe_unmatchable_candidates=safe_unmatchable,\n    )',
    ),
]

PRINT_OLD = '    print(f"Sem identificacao de negocio suficiente: {recurrence.unmatchable_candidates}")'
PRINT_NEW = (PRINT_OLD + '\n    print(f"Sem documento, mas sem conflito com historico/lote: {recurrence.safe_unmatchable_candidates}")'
    + '\n    print(f"Candidatos que exigem revisao antes de publicar: {recurrence.publication_blockers}")')

def replace_exact(source: str, old: str, new: str) -> str:
    if new in source:
        return source
    if source.count(old) != 1:
        raise RuntimeError("Codigo local diverge do esperado; nenhuma escrita realizada. Confira o diff e o branch.")
    return source.replace(old, new, 1)

def transformed_recurrence(source: str) -> str:
    for old, new in PATCHES:
        source = replace_exact(source, old, new)
    return source

def transformed_persistence(source: str) -> str:
    return replace_exact(source, PRINT_OLD, PRINT_NEW)

def main(argv=None):
    parser = argparse.ArgumentParser(description="Ajuste seguro da 7.6 para eventos sem documento fora do escopo historico")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Confere compatibilidade sem escrever")
    mode.add_argument("--apply", action="store_true", help="Aplica ajuste local após conferir ambos os arquivos")
    args = parser.parse_args(argv)
    if not RECURRENCE.is_file() or not PERSISTENCE.is_file():
        raise RuntimeError("Arquivos de destino ausentes; execute na raiz do repositorio.")
    existing_recurrence = RECURRENCE.read_text(encoding="utf-8")
    existing_persistence = PERSISTENCE.read_text(encoding="utf-8")
    new_recurrence = transformed_recurrence(existing_recurrence)
    new_persistence = transformed_persistence(existing_persistence)
    if args.check:
        print("7.6 E2E: estrutura compativel; ajuste local pode ser aplicado.")
        return 0
    if new_recurrence != existing_recurrence:
        RECURRENCE.write_text(new_recurrence, encoding="utf-8")
    if new_persistence != existing_persistence:
        PERSISTENCE.write_text(new_persistence, encoding="utf-8")
    print("7.6 E2E: ajuste aplicado somente em arquivos locais; execute os testes e a previa.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
