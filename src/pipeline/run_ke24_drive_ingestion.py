import pandas as pd
import json

from googleapiclient.discovery import build

from src.ingestion.google_drive_client import (
    authenticate,
    list_raw_xlsx_files,
    read_xlsx_from_drive,
)

from src.ingestion.prepare_ke24 import process_ke24_file

from src.privacy.privacy_runtime import load_privacy_runtime
from src.privacy.anonymize_ke24 import anonymize_ke24_dataframe
from src.privacy.validate_protected_dataset import (
    validate_protected_dataset,
)

from src.quality.validate_ke24 import (
    load_schema,
    load_quality_rules,
    validate_schema,
    validate_metadata,
    validate_required_fields,
    validate_integer_like_columns,
    validate_numeric_measures,
)

from src.loading.plan_ke24_incremental import (
    MANIFEST_COLUMNS,
    build_file_manifest,
    plan_incremental_load,
)

from src.processing.prepare_ke24_processed import (
    build_ke24_processed,
)

from io import BytesIO

from src.loading.ke24_drive_manifest import (
    load_processed_manifest,
)

from src.loading.ke24_drive_manifest import (
    load_processed_manifest,
    serialize_processed_manifest,
)

from src.loading.ke24_drive_publish import (
    preview_first_publication,
    publish_initial_batch,
)

from src.loading.ke24_drive_manifest import (
    load_processed_manifest,
    serialize_processed_manifest,
)

import argparse

from src.ingestion.google_drive_client import authenticate_write

def build_processed_parquet_buffer(processed, policy):
    #Gera o Parquet protegido diretamente em memória

    validate_protected_dataset(
        processed,
        policy,
        require_all_columns=True,
    )

    buffer = BytesIO()

    processed.to_parquet(
        buffer,
        index=False,
        engine="pyarrow",
    )

    # Confirma que o arquivo gerado pode ser lido.
    buffer.seek(0)

    restored = pd.read_parquet(buffer)

    # Confere se os dados foram preservados.
    pd.testing.assert_frame_equal(
        processed.reset_index(drop=True),
        restored.reset_index(drop=True),
        check_dtype=False,
    )

    buffer.seek(0)

    return buffer


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--publish-initial",
        action="store_true",
        help="Publica a primeira carga no Google Drive.",
    )

    args = parser.parse_args()
    print("\nKE24 - Integração Google Drive\n")

    # Carrega as regras de privacidade e qualidade.
    policy, privacy_schema, secret_key = load_privacy_runtime()
    schema = load_schema()
    quality_rules = load_quality_rules()

    # Conecta ao Google Drive.
    service = build(
        "drive",
        "v3",
        credentials=authenticate(),
        cache_discovery=False,
    )

    files = list_raw_xlsx_files(service)

    if not files:
        raise ValueError("Nenhum arquivo XLSX encontrado na pasta raw.")

    print(f"Arquivos encontrados: {len(files)}")

    dataframes = []
    expected_columns = None

    for index, file in enumerate(files, start=1):
        print(f"\nProcessando arquivo {index}/{len(files)}")

        # Recebe o Excel diretamente em memória.
        with read_xlsx_from_drive(service, file["id"]) as buffer:
            df, mapping, columns, row_count = process_ke24_file(
                buffer,
                expected_columns=expected_columns,
                source_name=file["name"],
            )

        # Aplica a política de proteção existente.
        protected_df = anonymize_ke24_dataframe(
            df=df,
            policy=policy,
            schema=privacy_schema,
            secret_key=secret_key,
        )

        validate_protected_dataset(
            protected_df,
            policy,
            require_all_columns=True,
        )

        if expected_columns is None:
            expected_columns = columns

        dataframes.append(protected_df)

        print(
            f"Arquivo {index}: "
            f"{len(protected_df)} registros protegidos."
        )

    # Consolida somente os dados já protegidos.
    staging = pd.concat(
        dataframes,
        ignore_index=True,
    )

    # Reutiliza as validações existentes.
    errors = []

    errors.extend(validate_schema(staging, schema))
    errors.extend(validate_metadata(staging))
    errors.extend(validate_required_fields(staging, quality_rules))
    errors.extend(validate_integer_like_columns(staging, quality_rules))
    errors.extend(
        validate_numeric_measures(staging, schema, quality_rules)
    )

    if errors:
        raise ValueError(
            "Validação de qualidade reprovada:\n"
            + "\n".join(errors)
        )

    # Verifica a consistência do manifesto de origem.
    manifest = build_file_manifest(staging)


    # Consulta o manifesto persistente no Google Drive.
    loaded_manifest = load_processed_manifest(service)

    plan = plan_incremental_load(
        staging_df=staging,
        loaded_manifest=loaded_manifest,
    )


    # Prepara o manifesto futuro, preservando cargas antigas.
    new_manifest = build_file_manifest(
        plan["new_rows"]
    ) if plan["new_files"] > 0 else pd.DataFrame(
        columns=MANIFEST_COLUMNS
    )

    updated_manifest = pd.concat(
        [
            loaded_manifest[MANIFEST_COLUMNS],
            new_manifest[MANIFEST_COLUMNS],
        ],
        ignore_index=True,
    )

    print("\nManifesto planejado:")
    print(f"Extrações anteriores: {len(loaded_manifest)}")
    print(f"Extrações novas: {len(new_manifest)}")
    print(f"Total após publicação: {len(updated_manifest)}")
    print("\nPlanejamento incremental:")
    print(f"Arquivos novos: {plan['new_files']}")
    print(f"Arquivos já processados: {plan['skipped_files']}")
    print(f"Registros novos: {len(plan['new_rows'])}")

    # Usa somente os registros ainda não publicados.
    new_staging = plan["new_rows"].copy()

    if new_staging.empty:
        print("\nNenhum arquivo novo para processar.")
        print("Execução encerrada sem publicação.")
        return

    processed, measure_columns = build_ke24_processed(
        new_staging
    )

    print("\nCamada processed validada!")

    with build_processed_parquet_buffer(
        processed,
        policy,
    ) as parquet_buffer:

        size_bytes = parquet_buffer.getbuffer().nbytes

        print("\nParquet processed gerado em memória!")
        print(f"Tamanho: {size_bytes / 1024:.2f} KB")
        print(f"Registros: {len(processed)}")
        print(f"Colunas: {len(processed.columns)}")


        # Prepara o manifesto que seria publicado.
        manifest_bytes = serialize_processed_manifest(
            updated_manifest
        )

        parquet_name = preview_first_publication(
            service=service,
            new_manifest=new_manifest,
            parquet_buffer=parquet_buffer,
            manifest_bytes=manifest_bytes,
        )

        if args.publish_initial:
            if not loaded_manifest.empty:
                raise RuntimeError(
                    "A publicação inicial exige manifesto vazio."
                )

            write_service = build(
                "drive",
                "v3",
                credentials=authenticate_write(),
                cache_discovery=False,
            )

            publish_initial_batch(
                read_service=service,
                write_service=write_service,
                parquet_name=parquet_name,
                parquet_buffer=parquet_buffer,
                manifest_bytes=manifest_bytes,
            )

            print("\nPrimeira publicação concluída!")

        else:
            print("\nModo de verificação: nenhum upload.")

    print(f"Registros processados: {len(processed)}")
    print(f"Colunas processadas: {len(processed.columns)}")
    print(f"Medidas financeiras: {len(measure_columns)}")

    print("\nValidação concluída!")
    print("Status: APROVADO")

    print(f"Arquivos validados: {len(files)}")
    print(f"Registros consolidados: {len(staging)}")
    print(f"Extrações no manifesto: {len(manifest)}")
    
if __name__ == "__main__":
    main()
