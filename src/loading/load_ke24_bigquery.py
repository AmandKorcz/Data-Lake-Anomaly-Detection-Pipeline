from pathlib import Path 

import pandas as pd 
from google.cloud import bigquery

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "staging"
    / "ke24_staging.parquet"
)

GCP_PROJECT = "datalake-pipeline"
BQ_DATASET = "profitability_data"
BQ_TABLE = "KE24_staging"
BQ_LOCATION = "southamerica-east1"

TABLE_ID = (
    f"{GCP_PROJECT}."
    f"{BQ_DATASET}."
    f"{BQ_TABLE}"
)

#Validação do staging
def validate_input_file(df):
    required_metadata = [
        "_load_id",
        "_source_file_sha256",
        "_source_system",
        "_source_file",
        "_source_row_number",
        "_ingested_at_utc"
    ]

    missing_columns = [
        column 
        for column in required_metadata
        if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            "O staging não possui metadados necesários para a carga no BigQuery: "
            +", ".join(missing_columns)
        )

    if df.empty:
        raise ValueError("O staging não possui registros")

    if df["_source_file_sha256"].isna().any():
        raise ValueError(
            "Existem registros sem '_source_file_sha256'"
        )

    empty_hash = (
        df["_source_file_sha256"]
        .astype("string")
        .str.strip()
        .eq("")
        .fillna(False)
    )

    if empty_hash.any():
        raise ValueError(
            "Existem registros com '_source_file_sha256' vazio"
        )

#Conexão com o BigQuery
def create_bigquery_client():
    return bigquery.Client(
        project=GCP_PROJECT
    )

#Consulta arquivos já carregados
def get_loaded_file_hashes(client):

    query = f"""
        SELECT DISTINCT 
            _source_file_sha256
        FROM `{TABLE_ID}`
        WHERE _source_file_sha256 IS NOT NULL
    """

    query_job = client.query(
        query, 
        location=BQ_LOCATION
    )

    result = query_job.result()

    loaded_hashes = {
        row["_source_file_sha256"]
        for row in result
    }

    return loaded_hashes

#Idenificação de novos arquivos
def filter_new_files(df, loaded_hashes):
    new_data = df[
        ~df["_source_file_sha256"].isin(loaded_hashes)
    ].copy()

    return new_data

#Carga BigQuery
def load_to_bigquery(client, df):

    #Schema da tabela já existente
    target_table = client.get_table(TABLE_ID)

    job_config = bigquery.LoadJobConfig(
        schema = target_table.schema,
        write_disposition = (bigquery.WriteDisposition.WRITE_APPEND),
    )

    load_job = client.load_table_from_dataframe(
        df, 
        TABLE_ID,
        job_config = job_config,
        location = BQ_LOCATION
    )

    load_job.result()

    return load_job

#Execução principal 
def main():
    print("\nKE24 - Carga Bigquery\n")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Arquivo staging não encontrado: {INPUT_FILE}"
        )

    print(f"Arquivo aalisado: {INPUT_FILE.name}")

    df = pd.read_parquet(INPUT_FILE)

    print(f"Registros disponíveis no staging: {len(df):,}")

    validate_input_file(df)

    client = create_bigquery_client()

    print(f"Projeto GCP: {GCP_PROJECT}")

    print(f"Tabela destino: {TABLE_ID}")

    loaded_hashes = get_loaded_file_hashes(client)

    print(f"Arquivos já carregados no BigQuery: {len(loaded_hashes)}")

    new_data = filter_new_files(
        df,
        loaded_hashes
    )

    if new_data.empty:
        print("\nNenhum arquivo novo para carregar")
        print("Carga encerrada sem alterações")

        return 

    new_files = (
        new_data["_source_file"]
        .drop_duplicates()
        .tolist()
    )

    print("\n Arquivos novos identificados: ")

    for file_name in new_files:
        print(f"= {file_name}")

        print(
            f"\nRegistros a carregar: "
            f"{len(new_data):,}"
        )

        load_to_bigquery(
            client, 
            new_data
        )

        print("\nCarga concluída com sucesso!")

        update_table = client.get_table(TABLE_ID)

        print(f"Registros atuais no BigQuery: {update_table.num_rows:,}")

if __name__ == "__main__":
    main()
