from pathlib import Path 

import pandas as pd 
from google.cloud import bigquery
from src.privacy.privacy_runtime import load_privacy_runtime

from src.loading.plan_ke24_incremental import (
    FILE_ID,
    BUSINESS_KEY,
    MANIFEST_COLUMNS,
    plan_incremental_load,
)

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

PROTECTED_BQ_TABLE = "KE24_staging_protected"

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
            "O staging não possui metadados necessários para a carga no BigQuery: "
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


def validate_bigquery_release(df=None):
    #Verifica se a carga pode utilizar o destino protegido.

    expected_table_id = (
        f"{GCP_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"
    )

    if (
        BQ_TABLE != PROTECTED_BQ_TABLE
        or TABLE_ID != expected_table_id
    ):
        raise RuntimeError(
            "Carga BigQuery bloqueada: destino diferente da tabela reservada para dados protegidos."
        )

    # Valida a política corporativa e a configuração da chave
    policy, schema, _ = load_privacy_runtime()

    if policy.get("status") != "APPROVED":
        raise RuntimeError(
            "Carga BigQuery bloqueada: política não aprovada."
        )

    # Permite verificar apenas a configuração sem receber um DataFrame.
    if df is None:
        return

    validate_input_file(df)

    expected_columns = (
        set(schema["columns"])
        | set(policy["metadata_actions"])
    )

    if (
        df.columns.duplicated().any()
        or set(df.columns) != expected_columns
    ):
        raise ValueError(
            "Layout incompatível com o contrato protegido."
        )

    token_columns = list(policy["tokenize_dimensions"])

    token_columns.extend(
        column
        for column, action in policy["metadata_actions"].items()
        if action == "tokenize"
    )

    # Confere o formato dos identificadores protegidos
    for column in token_columns:
        values = df[column].astype("string")

        valid_tokens = (
            values.str.fullmatch(r"TKN_[0-9a-f]{32}")
            .fillna(False)
        )

        invalid_values = (
            values.notna() & ~valid_tokens
        )

        if invalid_values.any():
            raise ValueError(
                f"A coluna '{column}' contém valores "
                "não tokenizados."
            )

    # Esses metadados e identificadores são obrigatórios
    required_identifiers = [
        "company_code",
        "_source_file",
        "_source_file_sha256",
    ]

    for column in required_identifiers:
        if df[column].isna().any():
            raise ValueError(
                f"Identificador obrigatório ausente: {column}"
            )

#Conexão com o BigQuery
def create_bigquery_client():
    return bigquery.Client(
        project=GCP_PROJECT
    )

#Consulta somente metadados da tabela protegida, sem ler medidas financeiras nem gravar dados
def get_loaded_file_manifest(client):

    # Exige destino protegido e política autorizada
    validate_bigquery_release()

    target_table = client.get_table(TABLE_ID)

    required_columns = {
        FILE_ID,
        "_source_file",
        "_source_row_number",
        *BUSINESS_KEY,
    }

    available_columns = {
        field.name
        for field in target_table.schema
    }

    if not required_columns.issubset(available_columns):
        raise ValueError(
            "A tabela protegida não possui os "
            "metadados necessários."
        )

    query = f"""
        SELECT
            _source_file_sha256,
            _source_file,
            company_code,
            period_year,
            currency,
            COUNT(*) AS record_count,
            COUNT(DISTINCT _source_row_number)
                AS distinct_row_count
        FROM `{TABLE_ID}`
        GROUP BY
            _source_file_sha256,
            _source_file,
            company_code,
            period_year,
            currency
    """

    query_job = client.query(
        query,
        location=BQ_LOCATION,
    )

    records = [
        dict(row.items())
        for row in query_job.result()
    ]

    # Tabela protegida existente, mas sem registros
    if not records:
        return pd.DataFrame(
            columns=MANIFEST_COLUMNS
        )

    required_result = (
        set(MANIFEST_COLUMNS)
        | {"_source_file", "distinct_row_count"}
    )

    if any(
        not required_result.issubset(record)
        for record in records
    ):
        raise ValueError(
            "A consulta retornou um manifesto incompleto."
        )

    manifest = pd.DataFrame(records)

    if manifest[list(required_result)].isna().any().any():
        raise ValueError(
            "O manifesto remoto contém valores nulos."
        )

    # Os identificadores devem estar protegidos
    for column in [
        FILE_ID,
        "_source_file",
        "company_code",
    ]:
        valid_tokens = (
            manifest[column]
            .astype("string")
            .str.fullmatch(r"TKN_[0-9a-f]{32}")
            .fillna(False)
        )

        if not valid_tokens.all():
            raise ValueError(
                "O manifesto contém identificadores "
                "não tokenizados."
            )

    # Detecta cargas parciais e linhas duplicadas
    try:
        counts = pd.to_numeric(
            manifest["record_count"],
            errors="raise",
        )

        distinct_counts = pd.to_numeric(
            manifest["distinct_row_count"],
            errors="raise",
        )

    except (ValueError, TypeError) as error:
        raise ValueError(
            "O manifesto possui contagens inválidas."
        ) from error

    if (
        counts.le(0).any()
        or counts.mod(1).ne(0).any()
        or distinct_counts.ne(counts).any()
    ):
        raise ValueError(
            "A tabela protegida contém registros "
            "duplicados ou contagens inconsistentes."
        )

    if (
        manifest[FILE_ID].duplicated().any()
        or manifest.duplicated(BUSINESS_KEY).any()
    ):
        raise ValueError(
            "O manifesto remoto contém arquivos "
            "ou períodos duplicados."
        )

    return manifest[MANIFEST_COLUMNS].copy()

#Planeja a carga incremental
def preview_bigquery_incremental(client, staging_df):

    # Protege também o DataFrame local
    validate_bigquery_release(staging_df)

    remote_manifest = get_loaded_file_manifest(client)

    return plan_incremental_load(
        staging_df,
        remote_manifest,
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

    validate_bigquery_release(df)

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

    # Bloqueia destinos e políticas não autorizados
    validate_bigquery_release()

    print("\nKE24 - Carga BigQuery\n")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Arquivo staging não encontrado: {INPUT_FILE}"
        )

    print(f"Arquivo analisado: {INPUT_FILE.name}")

    df = pd.read_parquet(INPUT_FILE)

    print(f"Registros disponíveis no staging: {len(df):,}")

    validate_input_file(df)
    validate_bigquery_release(df)

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
        print(f"- {file_name}")

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
