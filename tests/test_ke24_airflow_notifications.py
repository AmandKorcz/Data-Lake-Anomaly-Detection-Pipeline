import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "airflow" / "dags"))

from ke24_notifications import notificar_falha

class TestKe24Notifications(unittest.TestCase):

    @patch("ke24_notifications.logger")
    def test_registra_falha_sem_dados_privados(self, logger):
        contexto = {
            "task_instance": SimpleNamespace(
                dag_id="ke24_anomaly_detection_pipeline",
                task_id="validacao",
            ),
            "exception": RuntimeError("DADO_FINANCEIRO_PRIVADO"),
        }

        notificar_falha(contexto)

        logger.error.assert_called_once_with(
            "KE24 | Falha operacional | DAG=%s | Tarefa=%s",
            "ke24_anomaly_detection_pipeline",
            "validacao",
        )

    @patch("ke24_notifications.logger")
    def test_contexto_sem_tarefa(self, logger):
        notificar_falha({})

        logger.error.assert_called_once_with(
            "KE24 | Falha operacional sem identificacao da tarefa."
        )


if __name__ == "__main__":
    unittest.main()