from google.api_core.exceptions import (
    NotFound,
    PreconditionFailed,
)

from src.loading.gcs_execution_lock import (
    LOCK_OBJECT_NAME,
    RUN_ID_PATTERN,
)

from src.loading.ke24_run_reconciliation import (
    JobObservation,
    LockObservation,
    expected_job_id,
)

BQ_PROJECT = "datalake-pipeline"
BQ_LOCATION = "southamerica-east1"


def observe_gcs_lock(storage_client, bucket_name):
    #Consulta o lock no GCS.
    if (
        not isinstance(bucket_name, str)
        or not bucket_name.strip()
    ):
        raise ValueError(
            "Bucket de controle inválido."
        )

    try:
        blob = (
            storage_client
            .bucket(bucket_name)
            .blob(LOCK_OBJECT_NAME)
        )

        blob.reload()

    except NotFound:
        return LockObservation(status="MISSING")

    except Exception:
        return LockObservation(status="UNKNOWN")

    # Confirma a geração observada
    try:
        generation = blob.generation

        if isinstance(generation, bool):
            raise ValueError

        generation = int(generation)

        if generation <= 0:
            raise ValueError

    except (TypeError, ValueError, OverflowError):
        return LockObservation(status="UNKNOWN")

    # Lê o conteúdo correspondente à geração
    try:
        owner = blob.download_as_text(
            if_generation_match=generation
        )

    except (NotFound, PreconditionFailed):
        return LockObservation(status="UNKNOWN")

    except Exception:
        return LockObservation(status="UNKNOWN")

    # O proprietário deve ser um run_id válido
    if (
        not isinstance(owner, str)
        or not RUN_ID_PATTERN.fullmatch(owner)
    ):
        return LockObservation(status="UNKNOWN")

    return LockObservation(
        status="PRESENT",
        owner_run_id=owner,
        generation=generation,
    )

def observe_bigquery_job(bigquery_client, run_id):
    #Consulta o estado de um job do BigQuery.
    job_id = expected_job_id(run_id)

    try:
        job = bigquery_client.get_job(
            job_id,
            project=BQ_PROJECT,
            location=BQ_LOCATION,
        )

    except NotFound:
        return JobObservation(
            status="NOT_FOUND",
            job_id=job_id,
        )

    except Exception:
        return JobObservation(
            status="UNKNOWN",
            job_id=job_id,
        )

    try:
        # Confere identidade e tipo do job
        if (
            job.job_id != job_id
            or job.project != BQ_PROJECT
            or job.location != BQ_LOCATION
            or job.job_type != "query"
        ):
            state = "UNKNOWN"

        # Um job pendente ainda não terminou
        elif job.state in {"PENDING", "RUNNING"}:
            state = "RUNNING"

        # Job finalizado: conferir resultado
        elif job.state == "DONE":

            if job.error_result is None:
                state = "SUCCEEDED"

            elif (
                isinstance(job.error_result, dict)
                and job.error_result
            ):
                state = "FAILED"

            else:
                state = "UNKNOWN"

        else:
            state = "UNKNOWN"

    except Exception:
        state = "UNKNOWN"

    return JobObservation(
        status=state,
        job_id=job_id,
    )
