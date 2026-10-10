from pathlib import Path
import csv
import json

#Diretório principal do projeto
PROJECT_ROOT = Path(__file__).resolve().parents[2]

SCHEMA_FILE = PROJECT_ROOT / "config" / "ke24_schema.json"

OUTPUT_FILE = (
    PROJECT_ROOT 
    / "data" 
    / "processed" 
    / "ke24_privacy_inventory.csv"
)

#Metadados adicionados durante a ingestão
METADATA_COLUMNS = [
    "_load_id",
    "_source_file_sha256",
    "_source_system",
    "_source_file",
    "_source_row_number",
    "_ingested_at_utc"
]

#Identificadores para tokenização
TOKEN_CANDIDATES = {
    "company_code",
    "plant",
    "sales_organization",
    "profit_center",
    "business_unit,"
    "reference_document",
    "customer",
    "customerhierarchy01",
    "customerhierarchy02",
    "customerhierarchy03",
    "ship_to_party",
    "product"
}

#Campos de cotexto que devem ser preservados
KEEP_CANDIDATES = {
    "currency_type",
    "currency",
    "period",
    "period_year"
}

def suggest_treatment(column, group):
    if column in {"_source_file", "source_file_sha256"}:
        return "REVISAR_RASTREABILIDADE"

    if group == "metadata":
        return "PRESERVAR_METADADO_AVALIAR"

    if column in TOKEN_CANDIDATES:
        return "PRESERVAR_VALOR_AVALIAR"

    return "REVISAR"

def main():
    with open(SCHEMA_FILE, "r", encoding="utf-8") as file:
        schema = json.load(file)

    columns = schema["columns"]
    expected_count = schema["expected_column_count"]

    if len(columns) != expected_count:
        raise ValueError("Quantidade de colunas diferente do contrato.")

    if len(set(columns)) != len(columns):
        raise ValueError("Existem colunas duplicadas no cotrato.")

    if "sales_quantity" not in columns:
        raise ValueError("Coluna sales_quantity não encontrada.")

    measure_start = columns.index("sales_quantity")

    rows = []

    for column in METADATA_COLUMNS + columns:
        if column in METADATA_COLUMNS:
            group = "metadata"
        elif columns.index(column) < measure_start:
            group = "dimension"
        else:
            group = "measure"

        rows.append({
            "column": column,
            "group": group,
            "suggested_treatment": suggest_treatment(column, group),
            "personal_data": "REVISAR",
            "corporate_confidentiality": "REVISAR",
            "required_for_analysis": "REVISAR"
        })

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print("\nKE24 - Inventário de Privacidade")
    print(f"Schema: {schema['schema_version']}")
    print(f"Colunas SAP: {len(columns)}")
    print(f"Metadados: {len(METADATA_COLUMNS)}")
    print(f"Dimensões: {measure_start}")
    print(f"Medidas: {len(columns) - measure_start}")
    print(f"Total inventariado: {len(rows)}")
    print(f"\nArquivo gerado: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()

