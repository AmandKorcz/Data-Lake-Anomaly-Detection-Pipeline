import csv
import json
from pathlib import Path

from src.features.build_ke24_ml_features import CONTEXT_COLUMNS


PROJECT_ROOT = Path(__file__).resolve().parents[2]

OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "curated"
    / "ke24_column_review.csv"
)

DASHBOARD_DIRECT = {
    "company_code",
    "profit_center",
    "product",
    "reference_document",
    "currency",
}

DASHBOARD_DERIVED = {
    "period",
    "period_year",
}


def load_json(filename):
    path = PROJECT_ROOT / "config" / filename

    with path.open(encoding="utf-8") as file:
        return json.load(file)


def main():
    schema = load_json("ke24_schema.json")
    policy = load_json("ke24_privacy_policy.json")

    columns = schema["columns"]

    if len(columns) != 167 or len(set(columns)) != 167:
        raise ValueError("Invalid KE24 schema")

    if policy["schema_version"] != schema["schema_version"]:
        raise ValueError("Schema and policy versions differ")

    start = columns.index("sales_quantity")

    dimensions = columns[:start]
    measures = columns[start:]

    keep = set(policy["keep_dimensions"])
    tokenize = set(policy["tokenize_dimensions"])

    if keep & tokenize or keep | tokenize != set(dimensions):
        raise ValueError("Incomplete dimension classification")

    rows = []

    metadata = policy["metadata_actions"]

    for column, action in metadata.items():
        rows.append({
            "column": column,
            "category": "metadata",
            "privacy_action": action,
            "ml_context": column in CONTEXT_COLUMNS,
            "ml_training": "NO",
            "dashboard_usage": "NO_DIRECT",
            "review_status": "PENDING",
        })

    for column in columns:
        if column in dimensions:
            category = "dimension"
            action = (
                "tokenize"
                if column in tokenize
                else "keep"
            )
            training = "NO"

        else:
            category = (
                "quantity_measure"
                if column == "sales_quantity"
                else "financial_measure"
            )

            action = "keep"
            training = "IF_ACTIVE"

        if column in DASHBOARD_DIRECT:
            dashboard = "DIRECT"
        elif column in DASHBOARD_DERIVED:
            dashboard = "DERIVED"
        else:
            dashboard = "NO_DIRECT"

        rows.append({
            "column": column,
            "category": category,
            "privacy_action": action,
            "ml_context": column in CONTEXT_COLUMNS,
            "ml_training": training,
            "dashboard_usage": dashboard,
            "review_status": "PENDING",
        })

    if len(rows) != 173:
        raise ValueError("Unexpected inventory size")

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_FILE.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=rows[0].keys(),
        )

        writer.writeheader()
        writer.writerows(rows)

    print("KE24 COLUMN REVIEW")
    print("Dimensions:", len(dimensions))
    print("Tokenized dimensions:", len(tokenize))
    print("Preserved dimensions:", len(keep))
    print("Measures:", len(measures))
    print("Metadata:", len(metadata))
    print("Total inventoried:", len(rows))
    print("Output:", OUTPUT_FILE)
    print("COLUMN REVIEW GENERATED")


if __name__ == "__main__":
    main()
