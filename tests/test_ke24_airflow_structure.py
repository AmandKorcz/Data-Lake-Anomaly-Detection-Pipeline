import sys
from datetime import timedelta
from pathlib import Path

import pytest


def test_publicacao_depende_da_resposta_humana():
    pytest.importorskip("airflow.sdk")
    dags = Path(__file__).resolve().parents[1] / "airflow" / "dags"
    sys.path.insert(0, str(dags))

    from ke24_branch_rules import PUBLICAR, SOMENTE_PREVIA
    from ke24_pipeline import ke24_pipeline

    dag = ke24_pipeline()
    approval = dag.get_task("aprovar_publicacao")
    assert approval.options == [PUBLICAR, SOMENTE_PREVIA]
    assert approval.defaults == [SOMENTE_PREVIA]
    assert approval.response_timeout == timedelta(hours=1)
    assert approval.upstream_task_ids == {"preflight"}
    assert dag.get_task("decidir_fluxo").downstream_task_ids == {
        "publicar_lote", "somente_previa"
    }
    assert {"preflight", "decidir_fluxo", "aprovar_publicacao"} <= (
        dag.get_task("publicar_lote").upstream_task_ids
    )
