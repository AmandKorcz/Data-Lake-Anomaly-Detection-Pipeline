import json
import unittest
from pathlib import Path

import pandas as pd

from src.privacy.anonymize_ke24 import (
    anonymize_ke24_dataframe,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Chave fictícia usada para o teste
TEST_KEY = b"CHAVE_FICTICIA_APENAS_PARA_TESTES_2026"


def load_json(filename):
    with open(
        PROJECT_ROOT / "config" / filename,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def create_fake_record(schema, company, customer, period):
    columns = schema["columns"]
    measure_start = columns.index("sales_quantity")

    record = {}

    # Preenche as 32 dimensões com códigos fictícios
    for column in columns[:measure_start]:
        record[column] = "EXEMPLO"

    # Preenche as 135 medidas com valores fictícios
    for column in columns[measure_start:]:
        record[column] = 0.0

    record.update({
        "company_code": company,
        "customer": customer,
        "ship_to_party": customer,
        "reference_document": "DOC_TESTE",
        "period": period,
        "period_year": f"{period:02d}.2026",
        "currency": "BRL",
        "revenue": 1500.0,
        "sales_quantity": 10.0,
        "_load_id": f"TEST_{company}_{period}",
        "_source_file_sha256": f"FAKE_HASH_{company}_{period}",
        "_source_system": "SAP_KE24",
        "_source_file": f"fake_{company}_{period}.xlsx",
        "_source_row_number": 2,
        "_ingested_at_utc": pd.Timestamp(
            "2026-01-01T00:00:00Z"
        ),
    })

    return record


class TestKE24Anonymization(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.schema = load_json("ke24_schema.json")
        cls.policy = load_json("ke24_privacy_policy.json")

        cls.original = pd.DataFrame([
            create_fake_record(cls.schema, "COMP_A", "CUS_001", 1),
            create_fake_record(cls.schema, "COMP_A", "CUS_001", 2),
            create_fake_record(cls.schema, "COMP_B", "CUS_001", 1),
        ])

        cls.protected = anonymize_ke24_dataframe(
            cls.original,
            cls.policy,
            cls.schema,
            TEST_KEY,
        )

    def test_same_customer_across_months(self):
        self.assertEqual(
            self.protected.loc[0, "customer"],
            self.protected.loc[1, "customer"],
        )

    def test_customer_isolated_by_company(self):
        self.assertNotEqual(
            self.protected.loc[0, "customer"],
            self.protected.loc[2, "customer"],
        )

    def test_business_partner_relationship(self):
        self.assertEqual(
            self.protected.loc[0, "customer"],
            self.protected.loc[0, "ship_to_party"],
        )

    def test_financial_values_preserved(self):
        for column in ["revenue", "sales_quantity"]:
            pd.testing.assert_series_equal(
                self.original[column],
                self.protected[column],
            )

    def test_metadata_protected(self):
        for column in ["_source_file", "_source_file_sha256"]:
            self.assertNotEqual(
                self.original.loc[0, column],
                self.protected.loc[0, column],
            )

    def test_structure_preserved(self):
        self.assertEqual(
            self.original.shape,
            self.protected.shape,
        )
        self.assertEqual(
            list(self.original.columns),
            list(self.protected.columns),
        )

    def test_original_dataframe_unchanged(self):
        self.assertEqual(
            self.original.loc[0, "customer"],
            "CUS_001",
        )

    
    def test_all_financial_measures_preserved(self):
        # Identifica as 135 medidas financeiras
        columns = self.schema["columns"]

        measure_start = columns.index("sales_quantity")
        measure_columns = columns[measure_start:]

        # Cria valores fictícios variados para todas as medidas
        test_df = self.original.copy()

        for index, column in enumerate(measure_columns):
            value = float(index) + 0.25

            test_df[column] = [
                value,
                -value,
                0.0,
            ]

        # Aplica a tokenização
        protected_df = anonymize_ke24_dataframe(
            test_df,
            self.policy,
            self.schema,
            TEST_KEY,
        )

        # Compara todas as medidas antes e depois
        pd.testing.assert_frame_equal(
            test_df[measure_columns],
            protected_df[measure_columns],
            check_exact=True,
        )

    def test_null_customer_preserved(self):
        # Simula um registro sem cliente informado
        test_df = self.original.copy()
        test_df.loc[0, "customer"] = pd.NA

        protected_df = anonymize_ke24_dataframe(
            test_df,
            self.policy,
            self.schema,
            TEST_KEY,
        )

        self.assertTrue(
            pd.isna(protected_df.loc[0, "customer"])
        )

    def test_missing_company_blocks_processing(self):
        # Simula um registro sem empresa de origem
        test_df = self.original.copy()
        test_df.loc[0, "company_code"] = pd.NA

        with self.assertRaises(ValueError):
            anonymize_ke24_dataframe(
                test_df,
                self.policy,
                self.schema,
                TEST_KEY,
            )

    
    def test_incoterms_preserved(self):
        # Cria três valores comerciais fictícios
        test_df = self.original.copy()

        test_df["incoterms"] = [
            "FOB",
            "CIF",
            "DAP",
        ]

        protected_df = anonymize_ke24_dataframe(
            test_df,
            self.policy,
            self.schema,
            TEST_KEY,
        )

        # O campo deve continuar com seus valores originais
        pd.testing.assert_series_equal(
            test_df["incoterms"],
            protected_df["incoterms"],
        )

    def test_sensitive_dimensions_tokenized(self):
        # Campos que devem receber identificadores protegidos
        sensitive_columns = [
            "company_code",
            "customer",
            "ship_to_party",
            "profit_center",
            "product",
            "reference_document",
            "business_segment",
            "market",
            "division",
        ]

        for column in sensitive_columns:
            original_value = self.original.loc[0, column]
            protected_value = self.protected.loc[0, column]

            self.assertNotEqual(
                original_value,
                protected_value,
            )

            self.assertTrue(
                protected_value.startswith("TKN_"),
                f"Campo não tokenizado: {column}",
            )




if __name__ == "__main__":
    unittest.main()
