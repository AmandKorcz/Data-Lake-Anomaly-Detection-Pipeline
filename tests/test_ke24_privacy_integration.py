import json
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.ingestion import prepare_ke24 as ingestion


PROJECT_ROOT = Path(__file__).resolve().parents[1]

TEST_KEY = b"CHAVE_FICTICIA_APENAS_PARA_TESTES_2026"


def load_json(filename):
    with open(
        PROJECT_ROOT / "config" / filename,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def create_fake_file(path, schema, company, period):
    columns = schema["columns"]
    measure_start = columns.index("sales_quantity")

    record = {}

    for column in columns[:measure_start]:
        record[column] = "TEST_VALUE"

    for column in columns[measure_start:]:
        record[column] = 0.0

    record.update({
        "company_code": company,
        "customer": "CUSTOMER_001",
        "ship_to_party": "CUSTOMER_001",
        "reference_document": "DOC_TESTE",
        "period": period,
        "period_year": f"{period:02d}.2026",
        "currency": "BRL",
        "sales_quantity": 10.0,
        "revenue": 1500.0,
    })

    pd.DataFrame([record]).to_excel(
        path,
        index=False,
    )


class TestKE24PrivacyIntegration(unittest.TestCase):

    def test_complete_ingestion_with_fake_data(self):
        schema = load_json("ke24_schema.json")
        policy = load_json("ke24_privacy_policy.json")

        # Diretório temporário: não utiliza data/raw real
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)

            raw_dir = temp_root / "raw"
            staging_dir = temp_root / "staging"

            raw_dir.mkdir()
            staging_dir.mkdir()

            # Três extrações fictícias
            fake_files = [
                ("COMP_A", 1),
                ("COMP_A", 2),
                ("COMP_B", 1),
            ]

            for company, period in fake_files:
                filename = (
                    f"fake_{company}_{period:02d}_2026.xlsx"
                )

                create_fake_file(
                    raw_dir / filename,
                    schema,
                    company,
                    period,
                )

            consolidated_file = (
                staging_dir / "ke24_staging.parquet"
            )

            mapping_file = (
                staging_dir / "ke24_column_mapping.csv"
            )

            # Substitui caminhos e configurações somente durante a execução deste teste.
            with (
                patch.object(ingestion, "RAW_DIR", raw_dir),
                patch.object(ingestion, "OUTPUT_DIR", staging_dir),
                patch.object(
                    ingestion,
                    "CONSOLIDATED_OUTPUT_FILE",
                    consolidated_file,
                ),
                patch.object(
                    ingestion,
                    "COLUMN_MAPPING_FILE",
                    mapping_file,
                ),
                patch.object(
                    ingestion,
                    "load_privacy_runtime",
                    return_value=(policy, schema, TEST_KEY),
                ),
            ):
                output_capture = io.StringIO()

                with redirect_stdout(output_capture):
                    ingestion.main()

                log_output = output_capture.getvalue()

            result = pd.read_parquet(consolidated_file)

            # Confirma que os logs não revelam arquivos originais
            for company, period in fake_files:
                original_name = f"fake_{company}_{period:02d}_2026.xlsx"

                self.assertNotIn(
                    original_name,
                    log_output
                )

            # Confirma que os hashes originais não aparecem no terminal
            self.assertNotIn(
                "SHA-256:",
                log_output
            )

            for original_file in raw_dir.glob("*.xlsx"):
                original_hash = ingestion.calculate_file_hash(original_file)

                self.assertNotIn(
                    original_hash,
                    log_output
                )

            # Confere a estrutura
            self.assertEqual(len(result), 3)
            self.assertEqual(len(result.columns), 173)

            # Confere a preservação dos valores financeiros
            self.assertTrue((result["revenue"] == 1500).all())
            self.assertTrue((result["sales_quantity"] == 10).all())

            # Confere a tokenização
            self.assertTrue(
                result["customer"].str.startswith("TKN_").all()
            )

            # Mesmo cliente e empresa em meses diferentes
            company_tokens = result["company_code"].tolist()

            self.assertEqual(
                company_tokens[0],
                company_tokens[1],
            )

            self.assertEqual(
                result.loc[0, "customer"],
                result.loc[1, "customer"],
            )

            # Mesmo código em empresas diferentes
            self.assertNotEqual(
                result.loc[0, "customer"],
                result.loc[2, "customer"],
            )

            # Confere o mapeamento de origem
            mapping = pd.read_csv(mapping_file)

            self.assertTrue(
                mapping["source_file"].str.startswith("TKN_").all()
            )

            # Nenhum Parquet individual deve usar
            # o nome original dos arquivos
            for file in staging_dir.glob("*.parquet"):
                self.assertNotIn("COMP_A", file.name)
                self.assertNotIn("COMP_B", file.name)


if __name__ == "__main__":
    unittest.main()
