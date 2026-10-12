import unittest

from src.ml.classify_ke24_alerts import classify_materiality
from src.ml.protect_ke24_alert_rules import protect_company_rule_keys
from src.privacy.anonymize_ke24 import generate_token

TEST_KEY = b"CHAVE_FICTICIA_APENAS_PARA_TESTES_2026"

class TestKE24AlertPrivacyIntegration(unittest.TestCase):

    def setUp(self):
        self.rules = {
            "rules_name": "KE24_TEST",
            "business_thresholds_by_company": {
                "COMP_A": {
                    "currency": "BRL",
                    "materiality_medium_min": 100.0,
                    "materiality_high_min": 300.0,
                }
            },
        }

    def test_materiality_with_tokenized_company(self):
        company_token = generate_token(
            value="COMP_A",
            column="company_code",
            company="COMP_A",
            secret_key=TEST_KEY,
        )

        rules = {
            "business_thresholds_by_company": {
                company_token: {
                    "currency": "BRL",
                    "materiality_medium_min": 100.0,
                    "materiality_high_min": 300.0,
                }
            }
        }

        row = {
            "company_code": company_token,
            "currency": "BRL",
            "total_absolute_value": 350.0,
        }

        self.assertEqual(
            classify_materiality(row, rules),
            ("HIGH", "high_materiality"),
        )

    def test_protect_company_rules(self):
        protected_rules = protect_company_rule_keys(
            self.rules,
            TEST_KEY,
        )

        company_token = generate_token(
            value="COMP_A",
            column="company_code",
            company="COMP_A",
            secret_key=TEST_KEY,
        )

        protected_companies = protected_rules[
            "business_thresholds_by_company"
        ]

        self.assertIn(company_token, protected_companies)
        self.assertNotIn("COMP_A", protected_companies)

        row = {
            "company_code": company_token,
            "currency": "BRL",
            "total_absolute_value": 350.0,
        }

        self.assertEqual(
            classify_materiality(row, protected_rules),
            ("HIGH", "high_materiality"),
        )

    def test_original_rules_remain_unchanged(self):
        original_company_rules = self.rules[
            "business_thresholds_by_company"
        ]["COMP_A"].copy()

        protect_company_rule_keys(self.rules, TEST_KEY)

        self.assertEqual(
            self.rules["business_thresholds_by_company"]["COMP_A"],
            original_company_rules,
        )

        self.assertEqual(
            self.rules["rules_name"],
            "KE24_TEST",
        )

    def test_different_companies_have_different_tokens(self):
        token_a = generate_token(
            "COMP_A", "company_code", "COMP_A", TEST_KEY
        )

        token_b = generate_token(
            "COMP_B", "company_code", "COMP_B", TEST_KEY
        )

        self.assertNotEqual(token_a, token_b)

    def test_reject_duplicate_company_rules(self):
        rules = {
            "business_thresholds_by_company": {
                "COMP_A": {"currency": "BRL"},
                " COMP_A ": {"currency": "BRL"},
            }
        }

        with self.assertRaisesRegex(
            ValueError, "regras duplicadas"
        ):
            protect_company_rule_keys(rules, TEST_KEY)

    def test_reject_invalid_secret_key(self):
        with self.assertRaises(ValueError):
            protect_company_rule_keys(
                self.rules,
                b"short_key",
            )

if __name__ == "__main__":
    unittest.main()
