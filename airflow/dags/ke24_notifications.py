import logging

logger = logging.getLogger("ke24.airflow")


def notificar_falha(context):

    task_instance = context.get("task_instance")

    if task_instance is None:
        logger.error("KE24 | Falha operacional sem identificacao da tarefa.")
        return

    dag_id = task_instance.dag_id
    task_id = task_instance.task_id

    # Nao registrar exception, traceback ou dados do contexto.
    logger.error(
        "KE24 | Falha operacional | DAG=%s | Tarefa=%s",
        dag_id,
        task_id,
    )