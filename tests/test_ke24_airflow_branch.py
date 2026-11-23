import sys
import unittest
from pathlib import Path

DAGS = Path(__file__).resolve().parents[1] / "airflow" / "dags"
sys.path.insert(0, str(DAGS))

from ke24_branch_rules import PUBLICAR, SOMENTE_PREVIA, selecionar_destino


class TestKe24AirflowBranch(unittest.TestCase):
    def test_publica_somente_com_aprovacao_explicita(self):
        self.assertEqual(selecionar_destino({"chosen_options": [PUBLICAR]}), "publicar_lote")

    def test_recusa_expiracao_ou_resposta_invalida_seguem_para_previa(self):
        for resposta in (
            {"chosen_options": [SOMENTE_PREVIA]},
            None,
            {},
            {"chosen_options": PUBLICAR},
            {"chosen_options": [PUBLICAR, SOMENTE_PREVIA]},
        ):
            with self.subTest(resposta=resposta):
                self.assertEqual(selecionar_destino(resposta), "somente_previa")
