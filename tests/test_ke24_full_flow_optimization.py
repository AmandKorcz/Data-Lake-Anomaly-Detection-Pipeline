from __future__ import annotations

import unittest
from unittest import mock

from tools import ke24_full_flow as flow

RAW_ZERO = "Preflight E2E: APROVADO\nExtracoes novas: 0\nRegistros novos: 0\n"
RAW_ONE = "Preflight E2E: APROVADO\nExtracoes novas: 1\nRegistros novos: 42\n"
ALERT_ZERO = (
    "Status: PREVIA APROVADA. Nenhum upload realizado.\n"
    "Candidatos que exigem revisao antes de publicar: 0\n"
    "Alertas novos: 0\nTotal de alertas no historico: 4063\n"
)
QUEUE_OK = "existing_alerts: 4063\nnew_alerts: 0\nrequests_not_authenticated: 1\npublished: False\n"
CONTEXT_OK = "queue_alerts: 4063\ncontext_existing: 4063\ncontext_new: 0\npublished: False\n"


class TestKe24Fast(unittest.TestCase):
    @staticmethod
    def make_runner(calls, *, raw=RAW_ZERO, alert=ALERT_ZERO, queue=QUEUE_OK,
                    context=CONTEXT_OK, bad_ingestion=False):
        def runner(label, module, args=(), *, verbose=False):
            args = tuple(args)
            calls.append((module, args))
            if module == flow.PRE:
                return flow.Result(0, raw)
            if module == flow.ING:
                if args == ("--publish-incremental",):
                    return flow.Result(0, "Lote incremental publicado e validado.\nStatus: APROVADO\n")
                reads = sum(1 for m, a in calls if m == flow.ING and not a)
                count = 1 if (raw == RAW_ONE and (reads == 1 or bad_ingestion)) else 0
                return flow.Result(0, f"Arquivos novos: {count}\nStatus: APROVADO\n")
            if module == flow.MODEL:
                return flow.Result(0, "Status: APROVADO\n")
            if module == flow.ALERT:
                return flow.Result(0, alert)
            if module == flow.QUEUE:
                return flow.Result(0, queue)
            if module == flow.CONTEXT:
                return flow.Result(0, context)
            raise AssertionError(f"Modulo inesperado: {module}")
        return runner

    def test_auto_sem_arquivos_novos_escolhe_fluxo_rapido(self):
        calls = []
        result = flow.execute(auto=True, runner=self.make_runner(calls))
        self.assertTrue(result.completed)
        self.assertEqual(result.writes, ())
        self.assertEqual([m for m, _ in calls],
                         [flow.PRE, flow.ALERT, flow.QUEUE, flow.CONTEXT])

    def test_auto_com_novos_arquivos_escolhe_fluxo_completo_em_previa(self):
        calls = []
        result = flow.execute(auto=True, runner=self.make_runner(calls, raw=RAW_ONE))
        self.assertFalse(result.completed)
        self.assertEqual(result.paused_at, "processed")
        self.assertEqual(calls, [(flow.PRE, ()), (flow.ING, ())])

    def test_auto_guiado_com_novos_arquivos_executa_todas_as_etapas(self):
        calls = []
        prompts = []
        def ask(prompt):
            prompts.append(prompt)
            return "PUBLICAR"
        result = flow.execute(auto=True, guided=True, ask=ask,
                              runner=self.make_runner(calls, raw=RAW_ONE))
        self.assertTrue(result.completed)
        self.assertEqual(result.writes, ("processed",))
        self.assertEqual(len(prompts), 1)
        self.assertIn((flow.MODEL, ()), calls)
        self.assertEqual(sum(m == flow.ING for m, _ in calls), 3)
        self.assertEqual(calls[0][0], flow.PRE)
        self.assertEqual(calls[-1][0], flow.CONTEXT)

    def test_auto_contagens_inconsistentes_bloqueia_sem_prosseguir(self):
        for raw in (
            "Preflight E2E: APROVADO\nExtracoes novas: 1\nRegistros novos: 0\n",
            "Preflight E2E: APROVADO\nExtracoes novas: 0\nRegistros novos: 24\n",
        ):
            calls = []
            with self.subTest(raw=raw), self.assertRaisesRegex(flow.FlowStopped, "inconsistentes"):
                flow.execute(auto=True, guided=True, ask=lambda _: "PUBLICAR",
                             runner=self.make_runner(calls, raw=raw))
            self.assertEqual(calls, [(flow.PRE, ())])

    def test_auto_preflight_reprovado_nao_executa_etapas_sequentes(self):
        calls = []
        raw = "Preflight E2E: REPROVADO\nExtracoes novas: 0\nRegistros novos: 0\n"
        with self.assertRaisesRegex(flow.FlowStopped, "confirmacao esperada"):
            flow.execute(auto=True, runner=self.make_runner(calls, raw=raw))
        self.assertEqual(calls, [(flow.PRE, ())])

    def test_auto_guiado_nega_confirmacao_e_nao_publica(self):
        calls = []
        result = flow.execute(auto=True, guided=True, ask=lambda _: "NAO",
                              runner=self.make_runner(calls, raw=RAW_ONE))
        self.assertFalse(result.completed)
        self.assertEqual(result.paused_at, "processed")
        self.assertNotIn((flow.ING, ("--publish-incremental",)), calls)

    def test_cli_padrao_ativa_selecao_automatica_sem_publicacao(self):
        with mock.patch.object(flow, "execute", return_value=flow.Summary(True, None, (), 4063)) as run:
            self.assertEqual(flow.main([]), 0)
        self.assertTrue(run.call_args.kwargs["auto"])
        self.assertFalse(run.call_args.kwargs["guided"])
        self.assertFalse(run.call_args.kwargs["fast"])

    def test_cli_guided_seleciona_fluxo_automatico_com_confirmacoes(self):
        with mock.patch.object(flow, "execute", return_value=flow.Summary(True, None, (), 4063)) as run:
            self.assertEqual(flow.main(["--guided"]), 0)
        self.assertTrue(run.call_args.kwargs["auto"])
        self.assertTrue(run.call_args.kwargs["guided"])
        self.assertFalse(run.call_args.kwargs["fast"])

    def test_fast_antigo_continua_aceito(self):
        with mock.patch.object(flow, "execute", return_value=flow.Summary(True, None, (), 4063)) as run:
            self.assertEqual(flow.main(["--fast"]), 0)
        self.assertFalse(run.call_args.kwargs["auto"])
        self.assertTrue(run.call_args.kwargs["fast"])

    def test_fast_sem_novos_arquivos_elimina_passos_redundantes(self):
        calls = []
        result = flow.execute(fast=True, runner=self.make_runner(calls))
        self.assertTrue(result.completed)
        self.assertEqual(result.alerts, 4063)
        self.assertEqual(result.writes, ())
        self.assertEqual([m for m, a in calls], [flow.PRE, flow.ALERT, flow.QUEUE, flow.CONTEXT])

    def test_padrao_preserva_sequencia_antiga(self):
        calls = []
        result = flow.execute(runner=self.make_runner(calls))
        self.assertTrue(result.completed)
        self.assertEqual([m for m, a in calls], [
            flow.PRE, flow.ING, flow.MODEL, flow.ALERT, flow.QUEUE, flow.CONTEXT,
        ])

    def test_fast_com_nova_extracao_nao_pula_ingestao(self):
        calls = []
        result = flow.execute(fast=True, runner=self.make_runner(calls, raw=RAW_ONE))
        self.assertFalse(result.completed)
        self.assertEqual(result.paused_at, "processed")
        self.assertIn((flow.ING, ()), calls)
        self.assertNotIn((flow.ING, ("--publish-incremental",)), calls)

    def test_fast_guided_exige_publicar(self):
        calls = []
        answers = []
        def ask(prompt):
            answers.append(prompt)
            return "PUBLICAR"
        result = flow.execute(fast=True, guided=True, ask=ask,
                              runner=self.make_runner(calls, raw=RAW_ONE))
        self.assertTrue(result.completed)
        self.assertEqual(result.writes, ("processed",))
        self.assertEqual(len(answers), 1)
        self.assertEqual(sum(1 for m, _ in calls if m == flow.ING), 3)
        self.assertNotIn((flow.MODEL, ()), calls)

    def test_guided_sem_confirmacao_nao_publica(self):
        calls = []
        result = flow.execute(fast=True, guided=True, ask=lambda _: "NAO",
                              runner=self.make_runner(calls, raw=RAW_ONE))
        self.assertFalse(result.completed)
        self.assertEqual(result.writes, ())
        self.assertNotIn((flow.ING, ("--publish-incremental",)), calls)

    def test_nova_extracao_que_nao_zera_depois_de_publicar_bloqueia(self):
        calls = []
        with self.assertRaisesRegex(flow.FlowStopped, "Idempotencia"):
            flow.execute(fast=True, guided=True, ask=lambda _: "PUBLICAR",
                         runner=self.make_runner(calls, raw=RAW_ONE, bad_ingestion=True))

    def test_alerta_ambiguo_bloqueia_publicacao(self):
        calls = []
        alert = ALERT_ZERO.replace("publicar: 0", "publicar: 1")
        with self.assertRaisesRegex(flow.FlowStopped, "BLOQUEADA"):
            flow.execute(fast=True, runner=self.make_runner(calls, alert=alert))
        self.assertNotIn((flow.ALERT, ("--publish",)), calls)

    def test_alertas_acima_de_5000_sao_bloqueados(self):
        calls = []
        alert = ALERT_ZERO.replace("historico: 4063", "historico: 5001")
        with self.assertRaisesRegex(flow.FlowStopped, "5000"):
            flow.execute(fast=True, runner=self.make_runner(calls, alert=alert))

    def test_fila_divergente_bloqueia(self):
        calls = []
        queue = QUEUE_OK.replace("existing_alerts: 4063", "existing_alerts: 4000")
        with self.assertRaisesRegex(flow.FlowStopped, "Historico e fila"):
            flow.execute(fast=True, runner=self.make_runner(calls, queue=queue))

    def test_contexto_divergente_bloqueia(self):
        calls = []
        context = CONTEXT_OK.replace("context_existing: 4063", "context_existing: 4000")
        with self.assertRaisesRegex(flow.FlowStopped, "Contexto e fila"):
            flow.execute(fast=True, runner=self.make_runner(calls, context=context))


if __name__ == "__main__":
    unittest.main()
