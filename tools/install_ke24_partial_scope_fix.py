from __future__ import annotations

import argparse
import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGET = PROJECT_ROOT / "src" / "review" / "ke24_review_recurrence.py"
MARKER = "KE24_PARTIAL_SCOPE_DISJOINT_V1"

HELPER = '''\n\ndef _valid_source_row_number(row):
    try:
        value = pd.to_numeric(row.get("_source_row_number"), errors="coerce")
        if pd.isna(value) or not float(value).is_integer() or value < 2:
            return None
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _provably_distinct_from_incomplete_history(current, historical_rows):
    candidate_partial = _business_key(current, allow_missing_document=True)
    if candidate_partial is None:
        return False
    candidate_partial = (candidate_partial[0], *candidate_partial[2:])
    candidate_file = _protected(current.get("_source_file_sha256"), required=False)
    candidate_line = _valid_source_row_number(current)
    candidate_currency = str(current["currency"]).strip().upper()

    for prior in historical_rows:
        if _protected(prior["reference_document"], required=False) is not None:
            return False  # Falta outro campo alem do documento.
        prior_partial = _business_key(prior, allow_missing_document=True)
        if prior_partial is None:
            return False  # Falta informacao para diferenciar.
        prior_partial = (prior_partial[0], *prior_partial[2:])
        if candidate_partial == prior_partial:
            return False  # Potencial recorrencia de negocio.
        if str(prior["currency"]).strip().upper() != candidate_currency:
            continue  # Moeda e parte obrigatoria do contrato de negocio.
        prior_file = _protected(prior.get("_source_file_sha256"), required=False)
        prior_line = _valid_source_row_number(prior)
        if (candidate_file is None or prior_file != candidate_file
                or candidate_line is None or prior_line is None
                or candidate_line == prior_line):
            return False  # Em exportacoes diferentes a diferenca parcial e incerta.
    return True
'''

OLD_MAP = '''    old_keys = defaultdict(list)
    unknown_old_scopes = set()
    current_keys = defaultdict(list)
'''
NEW_MAP = '''    old_keys = defaultdict(list)
    unknown_old_scopes = defaultdict(list)
    current_keys = defaultdict(list)
'''
OLD_COLLECT = '''        else:
            unknown_old_scopes.add((row["company_code"], row["period_key"]))
    for row in new_rows:
'''
NEW_COLLECT = '''        else:
            unknown_old_scopes[(row["company_code"], row["period_key"])].append(row)
    for row in new_rows:
'''
OLD_BRANCH = '''            elif (row["company_code"], row["period_key"]) in unknown_old_scopes:
                ambiguous += 1
            elif key in old_keys:
'''
OLD_BRANCH_OLDER = '''            elif (row["company_code"], row["period_key"]) in unknown_old_scopes:
                # Uma ocorrência antiga sem chave segura pode ser o mesmo lançamento.
                ambiguous += 1
            elif key in old_keys:
'''
DIAG_TARGET = PROJECT_ROOT / "tools" / "diagnose_ke24_ambiguities.py"

DIAG_PATCHES = (
    (
        "    _protected,\n    _validate_frame,",
        "    _protected,\n    _provably_distinct_from_incomplete_history,\n    _validate_frame,",
    ),
    (
        '''        if scope in unknown_old_scopes:
            reasons["historico_incompleto_mesmo_escopo"] += 1
            old_incomplete = unknown_old_scopes[scope]
''',
        '''        if scope in unknown_old_scopes:
            old_incomplete = unknown_old_scopes[scope]
            if (key not in old_key_counts and current_key_counts[key] == 1
                    and _provably_distinct_from_incomplete_history(row, old_incomplete)):
                reasons["historico_incompleto_distinto_por_proveniencia"] += 1
            else:
                reasons["historico_incompleto_mesmo_escopo"] += 1
''',
    ),
    (
        '    derived_count = sum(reasons.values())',
        '''    derived_count = (reasons["historico_incompleto_mesmo_escopo"]
                     + reasons["chave_completa_nao_exclusiva"])''',
    ),
    (
        '''        "ambiguidade_historico_incompleto": reasons["historico_incompleto_mesmo_escopo"],
''',
        '''        "ambiguidade_historico_incompleto": reasons["historico_incompleto_mesmo_escopo"],
        "distintos_por_proveniencia": reasons["historico_incompleto_distinto_por_proveniencia"],
''',
    ),
    (
        '''        ("Ambiguidades por historico incompleto no mesmo grupo", "ambiguidade_historico_incompleto"),
''',
        '''        ("Ambiguidades por historico incompleto no mesmo grupo", "ambiguidade_historico_incompleto"),
        ("Distintos por origem comprovada (sem suprimir)", "distintos_por_proveniencia"),
''',
    ),
    (
        '("Ambiguidades em grupo com historico sem documento", "ambiguos_em_escopo_com_historico_sem_documento")',
        '("Candidatos em grupo com historico sem documento", "ambiguos_em_escopo_com_historico_sem_documento")',
    ),
)


def patched_diagnostic(original: str) -> str:
    if '"distintos_por_proveniencia"' in original:
        return original
    result = original
    for old, new in DIAG_PATCHES:
        if result.count(old) != 1:
            raise RuntimeError("Diagnostico local diverge do pacote anterior; nenhuma edicao aplicada.")
        result = result.replace(old, new, 1)
    ast.parse(result)
    return result


NEW_BRANCH = '''            elif (row["company_code"], row["period_key"]) in unknown_old_scopes:
                # Historico sem chave completa: so dispensa bloqueio mediante
                # proveniencia comprovada, sem suprimir nem mesclar alertas.
                scope = (row["company_code"], row["period_key"])
                if not (key not in old_keys
                        and len(current_keys[key]) == 1
                        and _provably_distinct_from_incomplete_history(
                            row, unknown_old_scopes[scope])):
                    ambiguous += 1
            elif key in old_keys:
'''


def patched_source(original: str) -> str:
    if MARKER in original:
        return original
    substitutions = (
        ("\ndef _validate_frame(frame, label):\n", HELPER + "\ndef _validate_frame(frame, label):\n"),
        (OLD_MAP, NEW_MAP),
        (OLD_COLLECT, NEW_COLLECT),
        (OLD_BRANCH, NEW_BRANCH),
    )
    updated = original
    if updated.count(OLD_BRANCH) != 1 and updated.count(OLD_BRANCH_OLDER) == 1:
        substitutions = tuple((OLD_BRANCH_OLDER, NEW_BRANCH) if old == OLD_BRANCH else (old, new)
                              for old, new in substitutions)
    for old, new in substitutions:
        if updated.count(old) != 1:
            raise RuntimeError("Contrato de codigo local divergente; nenhuma edicao aplicada.")
        updated = updated.replace(old, new, 1)
    ast.parse(updated)
    return updated


def main(argv=None):
    parser = argparse.ArgumentParser(description="Instalador local de regra de recorrencia conservadora")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Confere compatibilidade, sem gravar")
    mode.add_argument("--apply", action="store_true", help="Aplica somente ajuste local")
    args = parser.parse_args(argv)
    if not TARGET.is_file():
        raise SystemExit("Modulo de recorrencia ausente. Nenhuma alteracao realizada.")
    original = TARGET.read_text(encoding="utf-8")
    updated = patched_source(original)
    diag_before = DIAG_TARGET.read_text(encoding="utf-8") if DIAG_TARGET.is_file() else None
    diag_after = patched_diagnostic(diag_before) if diag_before is not None else None
    if updated == original and diag_after == diag_before:
        print("7.6: ajustes ja instalados; nenhuma alteracao.")
        return 0
    if args.check:
        print("7.6: estrutura compativel; ajuste disponivel; nenhuma alteracao.")
        return 0
    changes = [(TARGET, original, updated)]
    if diag_before is not None:
        changes.append((DIAG_TARGET, diag_before, diag_after))
    written = []
    try:
        for path, before, after in changes:
            if before != after:
                tmp = path.with_name(path.name + ".ke24-tmp")
                try:
                    tmp.write_text(after, encoding="utf-8", newline="\n")
                    tmp.replace(path)
                finally:
                    tmp.unlink(missing_ok=True)
                written.append((path, before))
    except Exception:
        for path, before in reversed(written):
            path.write_text(before, encoding="utf-8", newline="\n")
        raise
    print("7.6: recorrencia e diagnostico ajustados somente em arquivos locais.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
