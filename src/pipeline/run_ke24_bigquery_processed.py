import argparse
from io import BytesIO

from google.cloud import bigquery
from googleapiclient.discovery import build

from src.ingestion.google_drive_client import authenticate
from src.loading.load_ke24_bigquery import BQ_DATASET, BQ_LOCATION, GCP_PROJECT
from src.pipeline.run_ke24_drive_features import load_verified_processed_dataframe
from src.pipeline.run_ke24_profitability_sheet import (
    get_profitability_spreadsheet_id,
    sync_profitability,
)
from src.privacy.privacy_runtime import load_privacy_runtime


TABLE_ID = f"{GCP_PROJECT}.{BQ_DATASET}.ke24_processed"
GROUP_COLUMNS = ("company_code", "period_key", "currency")


def publish_processed_snapshot(client, processed, expected_counts):
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    with BytesIO() as parquet:
        processed.to_parquet(
            parquet, engine="pyarrow", index=False, coerce_timestamps="us"
        )
        load_job = client.load_table_from_file(
            parquet,
            TABLE_ID,
            rewind=True,
            job_config=job_config,
            location=BQ_LOCATION,
        )
        load_job.result()

    columns = ", ".join(GROUP_COLUMNS)
    query = f"""
        SELECT {columns}, COUNT(*) AS record_count
        FROM `{TABLE_ID}`
        GROUP BY {columns}
    """
    rows = client.query(query, location=BQ_LOCATION).result()
    actual_counts = {
        tuple(row[column] for column in GROUP_COLUMNS): row["record_count"]
        for row in rows
    }
    if actual_counts != expected_counts:
        raise RuntimeError(
            "A tabela foi carregada, mas as contagens por empresa, "
            "competência e moeda divergiram da origem."
        )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Consolida os lotes KE24 processed do Drive para o BigQuery."
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help=f"Publica o retrato completo, substituindo os dados de {TABLE_ID}.",
    )
    parser.add_argument(
        "--sync-profitability",
        action="store_true",
        help="Atualiza o Google Sheets de rentabilidade após validar a carga no BigQuery.",
    )
    args = parser.parse_args(argv)
    if args.sync_profitability and not args.publish:
        parser.error("--sync-profitability exige --publish.")
    spreadsheet_id = get_profitability_spreadsheet_id() if args.sync_profitability else None

    policy, schema, _ = load_privacy_runtime()
    drive = build(
        "drive", "v3", credentials=authenticate(), cache_discovery=False
    )
    processed = load_verified_processed_dataframe(drive, policy, schema)
    if processed.empty or processed[list(GROUP_COLUMNS)].isna().any().any():
        raise ValueError("Processed sem registros ou com dimensões incompletas.")

    expected_counts = processed.groupby(
        list(GROUP_COLUMNS), observed=True
    ).size().to_dict()

    print(f"Destino: {TABLE_ID}")
    print(f"Registros validados no Drive: {len(processed):,}")
    print(f"Grupos de empresa, competência e moeda: {len(expected_counts):,}")

    if not args.publish:
        print("Prévia concluída. Use --publish para publicar a tabela.")
        return

    with bigquery.Client(project=GCP_PROJECT, location=BQ_LOCATION) as client:
        publish_processed_snapshot(client, processed, expected_counts)
        print("Publicação concluída. As contagens no BigQuery conferem com o Drive.")
        if spreadsheet_id is not None:
            sync_profitability(client, spreadsheet_id=spreadsheet_id)


if __name__ == "__main__":
    main()
