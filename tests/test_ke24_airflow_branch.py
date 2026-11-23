import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAGS = ROOT / "airflow" / "dags"

sys.path.insert(0, str(DAGS))

from ke24_branch_rules import selecionar_destino


class TestKe24AirflowBranch(unittest.TestCase):

    def test_sem_novas_extracoes(self):
        destino = selecionar_destino(0)

        self.assertEqual(destino, "validacao")

    def test_com_novas_extracoes(self):
        destino = selecionar_destino(3)

        self.assertEqual(destino, "aguardando_publicacao")

    def test_quantidade_negativa(self):
        with self.assertRaises(ValueError):
            selecionar_destino(-1)

    def test_valor_invalido(self):
        for valor in [None, "0", 0.0, True]:
            with self.subTest(valor=valor):
                with self.assertRaises(ValueError):
                    selecionar_destino(valor)


if __name__ == "__main__":
    unittest.main()