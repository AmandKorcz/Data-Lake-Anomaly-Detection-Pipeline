import re

PROJECT_DATASET = "datalake-pipeline.profitability_data"
TARGET_TABLE = f"{PROJECT_DATASET}.KE24_staging_protected"

BUFFER_PATTERN = re.compile(
    r"KE24_staging_protected_buffer_[0-9a-f]{32}"
)

COLUMN_PATTERN = re.compile(r"[a-z_][a-z0-9_]*")

REQUIRED_COLUMNS = {
    "company_code",
    "period",
    "period_year",
    "currency",
    "_source_file",
    "_source_file_sha256",
    "_source_row_number",
}

def build_manifest_query(table_reference):
    return f"""
        SELECT
            _source_file_sha256 AS file_id,
            ANY_VALUE(_source_file) AS source_file,
            ANY_VALUE(company_code) AS company_code,
            ANY_VALUE(period) AS period,
            ANY_VALUE(period_year) AS period_year,
            ANY_VALUE(currency) AS currency,
            COUNT(*) AS record_count,
            COUNT(DISTINCT _source_row_number)
                AS distinct_row_count,
            COUNT(DISTINCT TO_JSON_STRING(STRUCT(
                _source_file,
                company_code,
                period,
                period_year,
                currency
            ))) AS context_count
        FROM {table_reference}
        GROUP BY _source_file_sha256
    """

#Gera SQL pra revisão técnica
def build_transaction_sql(schema, policy, buffer_table):

    if not BUFFER_PATTERN.fullmatch(buffer_table):
        raise ValueError(
            "Nome da tabela buffer inválido."
        )

    if policy["schema_version"] != schema["schema_version"]:
        raise ValueError("Versões do schema incompatíveis.")

    schema_columns = list(schema["columns"])
    metadata = policy["metadata_actions"]

    columns = schema_columns + list(metadata)

    if (
        len(schema_columns) != 167
        or len(columns) != 173
        or len(set(columns)) != len(columns)
        or not REQUIRED_COLUMNS.issubset(columns)
    ):
        raise ValueError("Contrato KE24 incompatível.")

    if not all(
        COLUMN_PATTERN.fullmatch(column)
        for column in columns
    ):
        raise ValueError("Nome de coluna inválido.")

    dimensions = schema_columns[
        :schema_columns.index("sales_quantity")
    ]

    classified = (
        policy["keep_dimensions"]
        + policy["tokenize_dimensions"]
    )

    if (
        len(classified) != len(set(classified))
        or set(classified) != set(dimensions)
    ):
        raise ValueError(
            "Classificação de dimensões inválida."
        )

    required_metadata_actions = {
        "_source_file": "tokenize",
        "_source_file_sha256": "tokenize",
    }

    for column, action in required_metadata_actions.items():
        if metadata.get(column) != action:
            raise ValueError(
                "Metadados de origem devem ser tokenizados."
            )

    token_columns = (
        list(policy["tokenize_dimensions"])
        + [
            column
            for column, action in metadata.items()
            if action == "tokenize"
        ]
    )

    token_pattern = r"^TKN_[0-9a-f]{32}$"

    token_checks = " OR\n            ".join(
        (
            f"(`{column}` IS NOT NULL AND "
            f"NOT REGEXP_CONTAINS("
            f"CAST(`{column}` AS STRING), "
            f"r'{token_pattern}'))"
        )
        for column in token_columns
    )

    source_table = (
        f"`{PROJECT_DATASET}.{buffer_table}`"
    )

    target_table = f"`{TARGET_TABLE}`"

    insert_columns = ",\n            ".join(
        f"`{column}`"
        for column in columns
    )

    insert_values = ",\n            ".join(
        f"s.`{column}`"
        for column in columns
    )

    source_manifest = build_manifest_query(source_table)
    target_manifest = build_manifest_query(target_table)

    return f"""
BEGIN TRANSACTION;

CREATE TEMP TABLE ke24_source_manifest AS
{source_manifest};

CREATE TEMP TABLE ke24_target_manifest AS
{target_manifest};

ASSERT (
    SELECT COUNT(*) > 0
    FROM {source_table}
) AS 'Buffer vazio.';

ASSERT NOT EXISTS (
    SELECT 1
    FROM {source_table}
    WHERE
        _source_file_sha256 IS NULL
        OR _source_file IS NULL
        OR _source_row_number IS NULL
        OR company_code IS NULL
        OR period IS NULL
        OR period_year IS NULL
        OR currency IS NULL
        OR {token_checks}
) AS 'Buffer possui identificadores invalidos.';

ASSERT NOT EXISTS (
    SELECT 1
    FROM ke24_source_manifest
    WHERE
        context_count != 1
        OR record_count != distinct_row_count
) AS 'Buffer possui arquivos ou linhas inconsistentes.';

ASSERT NOT EXISTS (
    SELECT 1
    FROM ke24_target_manifest
    WHERE
        context_count != 1
        OR record_count != distinct_row_count
) AS 'Destino possui arquivos ou linhas inconsistentes.';

ASSERT NOT EXISTS (
    SELECT 1
    FROM ke24_source_manifest
    GROUP BY company_code, period, period_year, currency
    HAVING COUNT(*) > 1
) AS 'Buffer contem versoes concorrentes do mesmo mes.';

ASSERT NOT EXISTS (
    SELECT 1
    FROM ke24_target_manifest
    GROUP BY company_code, period, period_year, currency
    HAVING COUNT(*) > 1
) AS 'Destino possui mais de uma versao mensal.';

ASSERT NOT EXISTS (
    SELECT 1
    FROM ke24_source_manifest AS s
    JOIN ke24_target_manifest AS t
      ON s.file_id = t.file_id
    WHERE
        s.record_count != t.record_count
        OR s.source_file != t.source_file
        OR s.company_code != t.company_code
        OR s.period != t.period
        OR s.period_year != t.period_year
        OR s.currency != t.currency
) AS 'Arquivo existente apresenta metadados divergentes.';

ASSERT NOT EXISTS (
    SELECT 1
    FROM ke24_source_manifest AS s
    JOIN ke24_target_manifest AS t
      ON s.company_code = t.company_code
     AND s.period = t.period
     AND s.period_year = t.period_year
     AND s.currency = t.currency
    WHERE s.file_id != t.file_id
) AS 'Substituicao mensal requer revisao.';

ASSERT NOT EXISTS (
    SELECT 1
    FROM {source_table} AS s
    WHERE EXISTS (
        SELECT 1
        FROM {target_table} AS t
        WHERE t._source_file_sha256 =
              s._source_file_sha256
    )
    AND NOT EXISTS (
        SELECT 1
        FROM {target_table} AS t
        WHERE t._source_file_sha256 =
              s._source_file_sha256
          AND t._source_row_number =
              s._source_row_number
    )
) AS 'Arquivo existente apresenta linhas divergentes.';

MERGE {target_table} AS t
USING {source_table} AS s
ON t._source_file_sha256 = s._source_file_sha256
AND t._source_row_number = s._source_row_number
WHEN NOT MATCHED THEN
    INSERT (
            {insert_columns}
    )
    VALUES (
            {insert_values}
    );

COMMIT TRANSACTION;
""".strip()
