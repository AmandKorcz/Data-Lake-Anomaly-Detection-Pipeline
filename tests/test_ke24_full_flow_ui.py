from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from tools import ke24_full_flow as flow

RAW = "Extracoes novas: 0\nRegistros novos: 0\nPreflight E2E: APROVADO\n"
ALERTS = (
    "Status: PREVIA APROVADA. Nenhum upload realizado.\n"
    "Candidatos que exigem revisao antes de publicar: 0\n"
    "Alertas novos: 0\nTotal de alertas no historico: 4063\n"
)
QUEUE = "existing_alerts: 4063\nnew_alerts: 0\nrequests_not_authenticated: 1\npublished: False\n"
CONTEXT = "queue_alerts: 4063\ncontext_existing: 4063\ncontext_new: 0\npublished: False\n"


def runner(label, module, args=(), *, verbose=False):
    return flow.Result(0, {
        flow.PRE: RAW,
        flow.ALERT: ALERTS,
        flow.QUEUE: QUEUE,
        flow.CONTEXT: CONTEXT,
    }[module])


class TestTerminalUI(unittest.TestCase):
    def test_resumo_e_etapas_profissionais(self):
        buffer = StringIO()
        with redirect_stdout(buffer):
            report = flow.execute(auto=True, runner=runner)
        output = buffer.getvalue()
        self.assertTrue(report.completed)
        self.assertEqual(output.count(flow.SEPARATOR), 10)  # duas no cabecalho, 7 etapas e fim
        for i in range(1, 8):
            self.assertIn(f"[{i:02d}/07]", output)
        self.assertIn("Tipo de fluxo: RAPIDO", output)
        self.assertIn("Historico / fila / contexto: 4.063 alertas", output)
        self.assertIn("Revisoes nao autenticadas: 1", output)
        self.assertNotIn("Executando com compatibilidade IPv4", output)
        self.assertNotIn("published: False", output)

    def test_stdout_do_subprocesso_nao_e_exposto_no_modo_normal(self):
        class Process:
            stdout = StringIO("chave=privada\nStatus: APROVADO\n")
            def wait(self):
                return 0
        buffer = StringIO()
        with patch.object(flow.subprocess, "Popen", return_value=Process()):
            with redirect_stdout(buffer):
                result = flow.run_module("1/7 teste", flow.PRE)
        self.assertEqual(result.code, 0)
        self.assertIn("chave=privada", result.output)
        self.assertNotIn("chave=privada", buffer.getvalue())
        self.assertIn("Tempo:", buffer.getvalue())

    def test_verbose_expoe_stdout_ao_usuario_que_optar(self):
        class Process:
            stdout = StringIO("mensagem-de-diagnostico\n")
            def wait(self):
                return 0
        buffer = StringIO()
        with patch.object(flow.subprocess, "Popen", return_value=Process()):
            with redirect_stdout(buffer):
                flow.run_module("1/7 teste", flow.PRE, verbose=True)
        self.assertIn("mensagem-de-diagnostico", buffer.getvalue())

    def test_publicacao_exige_mesma_palavra_de_confirmacao(self):
        with redirect_stdout(StringIO()):
            self.assertFalse(flow.approve("etapa", lambda _: "ok"))
            self.assertTrue(flow.approve("etapa", lambda _: "PUBLICAR"))


if __name__ == "__main__":
    unittest.main()
