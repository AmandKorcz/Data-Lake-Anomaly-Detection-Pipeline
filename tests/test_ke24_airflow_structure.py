import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "airflow" / "dags"))

from ke24_branch_rules import selecionar_destino


class TestKe24AirflowStructure(unittest.TestCase):

    def test_novas_extracoes_selecionam_bloqueio(self):
        self.assertEqual(
            selecionar_destino(1),
            "aguardando_publicacao",
        )

    def test_fluxo_sem_novos_arquivos(self):
        self.assertEqual(
            selecionar_destino(0),
            "validacao",
        )


if __name__ == "__main__":
    unittest.main()