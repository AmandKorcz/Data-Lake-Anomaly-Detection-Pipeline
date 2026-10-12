import json
import os
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.ml.classify_ke24_alerts import (
    build_classification,
    validate_currency_basis,
    validate_dataset,
)
from src.ml.load_ke24_alert_rules import (
    PROJECT_ROOT,
    load_protected_alert_rules,
)
from src.ml.validate_ke24_alert_rules import validate_company_rules
from src.privacy.anonymize_ke24 import generate_token


TEST_KEY = b"CHAVE_FICTICIA_APENAS_PARA_TESTES_2026"


class TestKE24AlertRulesLoading(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.rules_path = (
            Path(self.temp_dir.name) / "private_rules.json"
        )

        self.private_rules = {
            "rules_name": "KE24_TEST",
            "status": "experimental_auto_classification",
            "currency_basis": {
                "currency_type": 10,
            },
            "business_thresholds_by_company": {
                "COMP_A": {
                    "currency": "BRL",
                    "materiality_medium_min": 100.0,
                    "materiality_high_min": 300.0,
                }
            },
            "technical_thresholds": {
                "anomaly_percentile_medium_min": 90,
                "anomaly_percentile_high_min": 95,
                "stability_medium_min": 40,
                "stability_high_min": 100,
            },
        }

        self.rules_path.write_text(
            json.dumps(self.private_rules),
            encoding="utf-8",
        )

        self.company_token = generate_token(
            "COMP_A",
            "company_code",
            "COMP_A",
            TEST_KEY,
        )

    def load_test_rules(self):
        # Simula aprovação apenas dentro do teste unitário
        with patch.dict(
            os.environ,
            {"KE24_ALERT_RULES_FILE": str(self.rules_path)},
        ):
            with patch(
                "src.ml.load_ke24_alert_rules.load_privacy_runtime",
                return_value=({}, {}, TEST_KEY),
            ):
                return load_protected_alert_rules()

    def test_loads_protected_rules(self):
        rules = self.load_test_rules()
        companies = rules["business_thresholds_by_company"]

        self.assertIn(self.company_token, companies)
        self.assertNotIn("COMP_A", companies)

    def test_classification_with_protected_rules(self):
        rules = self.load_test_rules()

        dataset = pd.DataFrame([{
            "company_code": self.company_token,
            "currency_type": 10,
            "currency": "BRL",
            "anomaly_rank": 1,
            "anomaly_score": 0.9,
            "anomaly_score_percentile": 99.0,
            "materiality_percentile": 95.0,
            "stability_rate": 100.0,
            "total_absolute_value": 350.0,
            "reference_document": "DOC_TEST",
            "review_status": "pending",
            "business_validation": None,
        }])

        validate_dataset(dataset)
        validate_currency_basis(dataset, rules)

        result = build_classification(dataset, rules)

        self.assertEqual(
            result.iloc[0]["materiality_level"],
            "HIGH",
        )
        self.assertEqual(
            result.iloc[0]["alert_level"],
            "HIGH",
        )
        self.assertEqual(
            result.iloc[0]["classification_status"],
            "EXPERIMENTAL_CLASSIFICATION",
        )

    def test_company_rule_validation_preserves_token(self):
        rules = self.load_test_rules()

        dataset = pd.DataFrame([{
            "company_code": self.company_token,
            "currency_type": 10,
            "currency": "BRL",
        }])

        result = validate_company_rules(dataset, rules)

        self.assertEqual(
            result.iloc[0]["configuration_status"],
            "CONFIGURED",
        )
        self.assertEqual(
            result.iloc[0]["materiality_status"],
            "READY",
        )

    def test_unapproved_policy_blocks_loading(self):
        with patch(
            "src.ml.load_ke24_alert_rules.load_privacy_runtime",
            side_effect=RuntimeError("Política não aprovada"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Política não aprovada",
            ):
                load_protected_alert_rules()

    def test_rejects_rules_inside_repository(self):
        local_path = (
            PROJECT_ROOT
            / "config"
            / "ke24_alert_rules.example.json"
        )

        with patch.dict(
            os.environ,
            {"KE24_ALERT_RULES_FILE": str(local_path)},
        ):
            with patch(
                "src.ml.load_ke24_alert_rules.load_privacy_runtime",
                return_value=({}, {}, TEST_KEY),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "fora do repositório",
                ):
                    load_protected_alert_rules()

    def test_rejects_invalid_json(self):
        self.rules_path.write_text(
            "{invalid_json",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ValueError,
            "JSON válido",
        ):
            self.load_test_rules()


if __name__ == "__main__":
    unittest.main()
