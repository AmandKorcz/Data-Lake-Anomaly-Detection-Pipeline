from __future__ import annotations

import pytest
from tools.ke24_full_flow import (
    ALERT, CONTEXT, ING, MODEL, PRE, QUEUE,
    FlowStopped, Result, approve, execute, number,
)

PRE_NEW = 'Extracoes novas: 2\nPreflight E2E: APROVADO\n'
PRE_ZERO = 'Extracoes novas: 0\nPreflight E2E: APROVADO\n'
ING_NEW = 'Arquivos novos: 2\nArquivos novos: 2\nStatus: APROVADO\n'
ING_ZERO = 'Arquivos novos: 0\nNenhum arquivo novo para processar. Nenhum upload realizado.\n'
ING_PUB = 'Lote incremental publicado e validado.\nStatus: APROVADO\n'
MODEL_OK = 'Status: APROVADO. Nenhum dado financeiro gravado no Drive ou em disco.\n'
AL_NEW = ('Candidatos que exigem revisao antes de publicar: 0\n'
          'Alertas novos: 3\nTotal de alertas no historico: 10\n'
          'Status: PREVIA APROVADA. Nenhum upload realizado.\n')
AL_ZERO = AL_NEW.replace('Alertas novos: 3', 'Alertas novos: 0')
AL_PUB = 'Status: PUBLICACAO VERIFICADA (10 alertas no historico).\n'
QU_NEW = 'existing_alerts: 7\nnew_alerts: 3\nrequests_not_authenticated: 1\npublished: False\n'
QU_ZERO = 'existing_alerts: 10\nnew_alerts: 0\nrequests_not_authenticated: 1\npublished: False\n'
QU_PUB = QU_NEW.replace('published: False', 'published: True')
CO_NEW = 'queue_alerts: 10\ncontext_existing: 7\ncontext_new: 3\npublished: False\n'
CO_ZERO = 'queue_alerts: 10\ncontext_existing: 10\ncontext_new: 0\npublished: False\n'
CO_PUB = CO_NEW.replace('published: False', 'published: True')


def runner_for(*sequence):
    history = []
    data = iter(sequence)
    def run(label, module, args, *, verbose=False):
        history.append((module, args))
        module_expected, args_expected, output = next(data)
        assert (module, args) == (module_expected, args_expected)
        return output if isinstance(output, Result) else Result(0, output)
    return run, history


def full():
    return [
        (PRE, (), PRE_NEW),
        (ING, (), ING_NEW),
        (ING, ('--publish-incremental',), ING_PUB),
        (ING, (), ING_ZERO),
        (MODEL, (), MODEL_OK),
        (ALERT, (), AL_NEW),
        (ALERT, ('--publish',), AL_PUB),
        (QUEUE, ('--preview',), QU_NEW),
        (QUEUE, ('--publish', '--confirm-publish'), QU_PUB),
        (QUEUE, ('--preview',), QU_ZERO),
        (CONTEXT, ('--preview',), CO_NEW),
        (CONTEXT, ('--publish', '--confirm-publish'), CO_PUB),
        (CONTEXT, ('--preview',), CO_ZERO),
    ]


def test_guided_runs_all_7_phases_and_demands_4_approvals():
    run, calls = runner_for(*full())
    approvals = []
    def ask(prompt):
        approvals.append(prompt)
        return 'PUBLICAR'
    s = execute(guided=True, runner=run, ask=ask)
    assert s.completed and s.alerts == 10
    assert s.writes == ('processed', 'alert_history', 'review_queue', 'review_context')
    assert len(approvals) == 4 and len(calls) == 13


def test_preview_with_raw_new_stops_without_write():
    run, calls = runner_for((PRE, (), PRE_NEW), (ING, (), ING_NEW))
    s = execute(runner=run, ask=lambda _: pytest.fail('preview prompted user'))
    assert not s.completed and s.paused_at == 'processed'
    assert s.writes == () and len(calls) == 2


def test_decline_first_confirmation_never_writes():
    run, calls = runner_for((PRE, (), PRE_NEW), (ING, (), ING_NEW))
    s = execute(guided=True, runner=run, ask=lambda _: 'nao')
    assert not s.completed and not s.writes and len(calls) == 2


def test_decline_after_ingestion_preserves_partial_completion_report():
    run, calls = runner_for(*full()[:6])
    answers = iter(['PUBLICAR', 'nao'])
    s = execute(guided=True, runner=run, ask=lambda _: next(answers))
    assert s.writes == ('processed',) and s.paused_at == 'historico de alertas'
    assert len(calls) == 6


def test_no_new_raw_alerts_pending_preview_stops_before_write():
    run, calls = runner_for((PRE, (), PRE_ZERO), (ING, (), ING_ZERO),
                            (MODEL, (), MODEL_OK), (ALERT, (), AL_NEW))
    s = execute(runner=run)
    assert s.paused_at == 'historico de alertas' and len(calls) == 4


def test_no_changes_checks_all_read_only():
    run, calls = runner_for((PRE, (), PRE_ZERO), (ING, (), ING_ZERO),
                            (MODEL, (), MODEL_OK), (ALERT, (), AL_ZERO),
                            (QUEUE, ('--preview',), QU_ZERO),
                            (CONTEXT, ('--preview',), CO_ZERO))
    s = execute(runner=run, ask=lambda _: pytest.fail('should not prompt'))
    assert s.completed and s.writes == () and s.alerts == 10
    assert len(calls) == 6


def test_error_in_preflight_stops():
    run, calls = runner_for((PRE, (), Result(1, 'Falha')))
    with pytest.raises(FlowStopped): execute(guided=True, runner=run)
    assert len(calls) == 1


def test_preflight_pending_stops():
    run, calls = runner_for((PRE, (), 'Extracoes novas: 2\nPreflight E2E: PENDENTE'))
    with pytest.raises(FlowStopped): execute(guided=True, runner=run)
    assert len(calls) == 1


def test_changed_raw_between_readings_stops():
    run, calls = runner_for((PRE, (), PRE_NEW), (ING, (), ING_ZERO))
    with pytest.raises(FlowStopped, match='mudou'): execute(guided=True, runner=run)
    assert len(calls) == 2


def test_no_approval_if_ingestion_preview_lacks_approval():
    run, calls = runner_for((PRE, (), PRE_NEW), (ING, (), 'Arquivos novos: 2'))
    with pytest.raises(FlowStopped): execute(guided=True, runner=run)
    assert len(calls) == 2


def test_ambiguous_recurring_alerts_block_publication():
    blocked = AL_NEW.replace('revisao antes de publicar: 0', 'revisao antes de publicar: 5')
    run, calls = runner_for((PRE, (), PRE_ZERO), (ING, (), ING_ZERO),
                            (MODEL, (), MODEL_OK), (ALERT, (), blocked))
    with pytest.raises(FlowStopped, match='BLOQUEADA'):
        execute(guided=True, runner=run, ask=lambda _: 'PUBLICAR')
    assert len(calls) == 4


def test_ingest_publish_missing_confirmation_stops_without_retry():
    run, calls = runner_for((PRE, (), PRE_NEW), (ING, (), ING_NEW),
                            (ING, ('--publish-incremental',), 'Status: APROVADO'))
    with pytest.raises(FlowStopped): execute(guided=True, runner=run, ask=lambda _: 'PUBLICAR')
    assert len(calls) == 3


def test_processed_idempotence_required():
    run, calls = runner_for((PRE, (), PRE_NEW), (ING, (), ING_NEW),
                            (ING, ('--publish-incremental',), ING_PUB),
                            (ING, (), ING_NEW))
    with pytest.raises(FlowStopped, match='Idempotencia'):
        execute(guided=True, runner=run, ask=lambda _: 'PUBLICAR')
    assert len(calls) == 4


def test_alerts_publish_different_total_stops():
    run, calls = runner_for((PRE, (), PRE_ZERO), (ING, (), ING_ZERO),
                            (MODEL, (), MODEL_OK), (ALERT, (), AL_NEW),
                            (ALERT, ('--publish',), 'Status: PUBLICACAO VERIFICADA (11 alertas no historico).'))
    with pytest.raises(FlowStopped): execute(guided=True, runner=run, ask=lambda _: 'PUBLICAR')
    assert len(calls) == 5


def test_queue_count_must_match_history():
    wrong = QU_NEW.replace('new_alerts: 3', 'new_alerts: 2')
    run, calls = runner_for((PRE, (), PRE_ZERO), (ING, (), ING_ZERO),
                            (MODEL, (), MODEL_OK), (ALERT, (), AL_ZERO),
                            (QUEUE, ('--preview',), wrong))
    with pytest.raises(FlowStopped, match='discordam'):
        execute(guided=True, runner=run, ask=lambda _: 'PUBLICAR')
    assert len(calls) == 5


def test_context_count_must_match_queue():
    wrong = CO_NEW.replace('context_new: 3', 'context_new: 2')
    run, calls = runner_for((PRE, (), PRE_ZERO), (ING, (), ING_ZERO),
                            (MODEL, (), MODEL_OK), (ALERT, (), AL_ZERO),
                            (QUEUE, ('--preview',), QU_ZERO), (CONTEXT, ('--preview',), wrong))
    with pytest.raises(FlowStopped, match='divergem'):
        execute(guided=True, runner=run, ask=lambda _: 'PUBLICAR')
    assert len(calls) == 6


def test_reject_inconsistent_numbers():
    with pytest.raises(FlowStopped): number('Arquivos novos: 2\nArquivos novos: 3', 'Arquivos novos')


def test_reject_missing_count():
    with pytest.raises(FlowStopped): number('Nenhum arquivo novo', 'Arquivos novos')


def test_requires_exact_confirmation():
    assert not approve('processed', lambda _: 'sim')
    assert not approve('processed', lambda _: 'publicar')
    assert approve('processed', lambda _: ' PUBLICAR ')


def test_eof_rejects_confirmation():
    def eof(_): raise EOFError()
    assert not approve('alertas', eof)


def test_publish_queue_not_confirmed_blocks_context():
    run, calls = runner_for((PRE, (), PRE_ZERO), (ING, (), ING_ZERO),
                            (MODEL, (), MODEL_OK), (ALERT, (), AL_ZERO),
                            (QUEUE, ('--preview',), QU_NEW),
                            (QUEUE, ('--publish', '--confirm-publish'), QU_NEW))
    with pytest.raises(FlowStopped): execute(guided=True, runner=run, ask=lambda _: 'PUBLICAR')
    assert len(calls) == 6


def test_guided_resume_after_processed_with_pending_alerts():
    run, calls = runner_for((PRE, (), PRE_ZERO), (ING, (), ING_ZERO),
                            (MODEL, (), MODEL_OK), (ALERT, (), AL_NEW),
                            (ALERT, ('--publish',), AL_PUB),
                            (QUEUE, ('--preview',), QU_NEW),
                            (QUEUE, ('--publish', '--confirm-publish'), QU_PUB),
                            (QUEUE, ('--preview',), QU_ZERO),
                            (CONTEXT, ('--preview',), CO_NEW),
                            (CONTEXT, ('--publish', '--confirm-publish'), CO_PUB),
                            (CONTEXT, ('--preview',), CO_ZERO))
    s = execute(guided=True, runner=run, ask=lambda _: 'PUBLICAR')
    assert s.completed and s.writes == ('alert_history', 'review_queue', 'review_context')


def test_queue_preview_nonzero_stops_before_context():
    run, calls = runner_for((PRE, (), PRE_ZERO), (ING, (), ING_ZERO),
                            (MODEL, (), MODEL_OK), (ALERT, (), AL_ZERO),
                            (QUEUE, ('--preview',), Result(1, 'Falhou')))
    with pytest.raises(FlowStopped): execute(guided=True, runner=run)
    assert len(calls) == 5


def test_capacity_checked_before_alert_history_publication():
    too_many = AL_NEW.replace("Total de alertas no historico: 10",
                              "Total de alertas no historico: 5001")
    run, calls = runner_for((PRE, (), PRE_ZERO), (ING, (), ING_ZERO),
                            (MODEL, (), MODEL_OK), (ALERT, (), too_many))
    with pytest.raises(FlowStopped, match='capacidade'):
        execute(guided=True, runner=run, ask=lambda _: 'PUBLICAR')
    assert len(calls) == 4
