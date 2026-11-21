from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "src/pipeline/run_ke24_drive_alert_persistence.py"
MARKER = "KE24_BUSINESS_RECURRENCE_V1"

ANCHOR_FUNC = "\ndef main():\n"
ANCHOR_CALL = '''    history, new_count, suppressed = prepare_alerts_respecting_reviews(
        current, state.history, statuses, policy
    )'''
ANCHOR_PRINT = '    print(f"Recorrencias ja tratadas na planilha: {suppressed}")'
ANCHOR_PREVIEW = '''    if not args.publish:
        print("\\nStatus: PREVIA APROVADA. Nenhum upload realizado.")
        return'''

NEW_FUNC = '''\n\ndef prepare_alerts_with_business_recurrence(current, previous_history, statuses, policy):
    # KE24_BUSINESS_RECURRENCE_V1: não altera IDs ou registros históricos.
    from src.review.ke24_review_recurrence import plan_business_recurrences
    plan = plan_business_recurrences(previous_history, current, statuses)
    history, new_count = append_new_alerts(previous_history, plan.eligible, policy)
    return history, new_count, plan
'''
NEW_CALL = '''    history, new_count, recurrence = prepare_alerts_with_business_recurrence(
        current, state.history, statuses, policy
    )'''
NEW_PRINT = '''    print(f"Recorrencias tratadas (ID ou identidade de negocio): {recurrence.suppressed_reviewed}")
    print(f"Reapresentacoes ainda pendentes reconhecidas: {recurrence.duplicates_pending}")
    print(f"Correspondencias entre exportacoes: {recurrence.matched_business}")
    print(f"Correspondencias ambiguas: {recurrence.ambiguous_candidates}")
    print(f"Sem identificacao de negocio suficiente: {recurrence.unmatchable_candidates}")'''
NEW_PREVIEW = '''    if not args.publish:
        if recurrence.publication_blockers:
            print("ATENCAO: candidatos sem correspondencia segura; publicacao bloqueada ate revisao.")
        print("\\nStatus: PREVIA APROVADA. Nenhum upload realizado.")
        return
    if recurrence.publication_blockers:
        raise RuntimeError(
            "Publicacao bloqueada: identidade de negocio ausente ou ambigua. "
            "Revise a previa antes de publicar novos alertas."
        )'''


def transform(source: str) -> str:
    if MARKER in source:
        if ("prepare_alerts_with_business_recurrence(" in source
            and "recurrence.publication_blockers" in source):
            return source
        raise RuntimeError("Integracao 7.6 parcial; revisar sem sobrescrever.")
    for anchor in (ANCHOR_FUNC, ANCHOR_CALL, ANCHOR_PRINT, ANCHOR_PREVIEW):
        if source.count(anchor) != 1:
            raise RuntimeError("Pipeline diverge do contrato conhecido; nao alterar automaticamente.")
    result = source.replace(ANCHOR_FUNC, NEW_FUNC + ANCHOR_FUNC, 1)
    result = result.replace(ANCHOR_CALL, NEW_CALL, 1)
    result = result.replace(ANCHOR_PRINT, NEW_PRINT, 1)
    result = result.replace(ANCHOR_PREVIEW, NEW_PREVIEW, 1)
    compile(result, str(TARGET), "exec")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Instalacao offline KE24 7.6")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    source = TARGET.read_text(encoding="utf-8")
    proposed = transform(source)
    if proposed == source:
        print("7.6: integracao ja aplicada; nenhuma alteracao.")
        return 0
    if args.check:
        print("7.6: estrutura compativel; instalacao pode ser aplicada.")
        return 0
    descriptor, temporary = tempfile.mkstemp(
        dir=str(TARGET.parent), prefix=".ke24_recurrence_", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as dest:
            dest.write(proposed)
        os.replace(temporary, TARGET)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("7.6: integracao aplicada localmente; execute testes e previa.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
