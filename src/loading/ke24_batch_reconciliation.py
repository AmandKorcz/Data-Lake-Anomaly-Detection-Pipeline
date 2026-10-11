import re

import pandas as pd

from src.loading.ke24_execution_control import (
    ExecutionDecision,
)
from src.loading.ke24_run_reconciliation import (
    reconcile_registered_run,
)
from src.loading.ke24_state_observers import (
    observe_bigquery_job,
    observe_gcs_lock,
)
from src.loading.plan_ke24_incremental import (
    BUSINESS_KEY,
    FILE_ID,
    MANIFEST_COLUMNS,
)

TOKEN_PATTERN = re.compile(r"TKN_[0-9a-f]{32}")

class DuplicateManifestError(ValueError):
    pass

def _index_manifest(manifest, *, allow_empty):
#Valida somente metadados dos arquivos. Não acessa valores financeiros.

    if (
        not isinstance(manifest, pd.DataFrame)
        or manifest.columns.duplicated().any()
        or not set(MANIFEST_COLUMNS).issubset(
            manifest.columns
        )
    ):
        raise ValueError(
            "Layout do manifesto inválido."
        )

    if manifest.empty:
        if allow_empty:
            return {}, {}

        raise ValueError(
            "Lote esperado vazio."
        )

    data = manifest[MANIFEST_COLUMNS].copy()

    if data.isna().any().any():
        raise ValueError(
            "Manifesto com campos nulos."
        )

    # Identificadores protegidos
    for field in (FILE_ID, "company_code"):
        valid = data[field].map(
            lambda value: (
                isinstance(value, str)
                and TOKEN_PATTERN.fullmatch(value)
                is not None
            )
        )

        if not valid.all():
            raise ValueError(
                "Identificador protegido inválido."
            )

    # Campos necessários para identificar uma extração mensal
    for field in BUSINESS_KEY:
        blank = (
            data[field]
            .astype("string")
            .str.strip()
            .eq("")
        )

        if blank.any():
            raise ValueError(
                "Chave mensal vazia."
            )

    # Verifica as quantidades de registros
    if data["record_count"].map(
        lambda value: isinstance(value, bool)
    ).any():
        raise ValueError(
            "Contagem inválida."
        )

    counts = pd.to_numeric(
        data["record_count"],
        errors="raise",
    )

    if (
        counts.le(0).any()
        or counts.isna().any()
        or counts.mod(1).ne(0).any()
    ):
        raise ValueError(
            "Contagem inválida."
        )

    # Não aceita versões duplicadas
    if (
        data[FILE_ID].duplicated().any()
        or data.duplicated(BUSINESS_KEY).any()
    ):
        raise DuplicateManifestError(
            "Extrações duplicadas."
        )

    by_file = {}
    by_month = {}

    normalized = data.assign(
        record_count=counts.astype("int64")
    )

    for row in normalized.to_dict("records"):
        file_id = row[FILE_ID]

        month = tuple(
            row[field]
            for field in BUSINESS_KEY
        )

        by_file[file_id] = (
            month,
            row["record_count"],
        )

        by_month[month] = file_id

    return by_file, by_month


def assess_exact_batch(
    expected_manifest,
    remote_manifest,
):
    try:
        expected_files, _ = _index_manifest(
            expected_manifest,
            allow_empty=False,
        )

    except (ValueError, TypeError, OverflowError):
        return "UNVERIFIED"

    try:
        remote_files, remote_months = (
            _index_manifest(
                remote_manifest,
                allow_empty=True,
            )
        )

    except DuplicateManifestError:
        return "CONFLICT"

    except (ValueError, TypeError, OverflowError):
        return "UNVERIFIED"

    missing = False

    for file_id, (month, count) in (
        expected_files.items()
    ):
        existing = remote_files.get(file_id)

        # Mesmo arquivo com período, empresa, moeda ou quantidade divergente
        if (
            existing is not None
            and existing != (month, count)
        ):
            return "CONFLICT"

        # Mesmo mês associado a outro arquivo
        if remote_months.get(month) not in (
            None,
            file_id,
        ):
            return "CONFLICT"

        if existing is None:
            missing = True

    if missing:
        return "NEW_FILES"

    return "ALREADY_LOADED"

def reconcile_observed_batch(
    *,
    run_journal,
    storage_client,
    bucket_name,
    bigquery_client,
    expected_manifest,
    remote_manifest,
):

    manifest_status = assess_exact_batch(
        expected_manifest,
        remote_manifest,
    )

    try:
        lock = observe_gcs_lock(
            storage_client,
            bucket_name,
        )

        job = observe_bigquery_job(
            bigquery_client,
            run_journal.run_id,
        )

        return reconcile_registered_run(
            run_journal=run_journal,
            lock_observation=lock,
            job_observation=job,
            manifest_status=manifest_status,
        )

    except Exception:
        return ExecutionDecision(
            action="RECONCILE",
            reason=(
                "Não foi possível confirmar "
                "as evidências da tentativa."
            ),
        )
