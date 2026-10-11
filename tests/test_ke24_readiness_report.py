import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.loading.ke24_readiness_report import (
    build_readiness_report,
    main,
)

METADATA = {
    "_load_id": "keep",
    "_source_file_sha256": "tokenize",
    "_source_system": "keep",
    "_source_file": "tokenize",
    "_source_row_number": "keep",
    "_ingested_at_utc": "keep",
}

LOADER = '''
BQ_TABLE = "KE24_staging"
PROTECTED_BQ_TABLE = "KE24_staging_protected"

def load_to_bigquery(client, df):
    validate_bigquery_release(df)
    raise RuntimeError("Gravação BigQuery desabilitada: operação ainda não implementada.")
'''

class TestKE24ReadinessReport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "src" / "loading").mkdir(parents=True)
        dimensions = ["period", "company_code", "customer"]
        schema = {
            "schema_version": "1.1",
            "columns": dimensions + ["sales_quantity"] + [
                f"financial_measure_{i}" for i in range(163)
            ],
        }
        policy = {
            "status": "DRAFT_REVIEW_REQUIRED",
            "schema_version": "1.1",
            "keep_dimensions": ["period"],
            "tokenize_dimensions": ["company_code", "customer"],
            "metadata_actions": METADATA.copy(),
            "measure_policy": {"start_column": "sales_quantity", "action": "keep"},
        }
        self.schema = schema
        self.policy = policy
        self.save()
        self.loader_path().write_text(LOADER, encoding="utf-8")

    def loader_path(self):
        return self.root / "src" / "loading" / "load_ke24_bigquery.py"

    def save(self):
        (self.root / "config" / "ke24_privacy_policy.json").write_text(
            json.dumps(self.policy), encoding="utf-8"
        )
        (self.root / "config" / "ke24_schema.json").write_text(
            json.dumps(self.schema), encoding="utf-8"
        )

    def report(self):
        return build_readiness_report(self.root)

    def test_draft_policy_is_blocked(self):
        self.assertEqual(self.report()["checks"]["privacy_policy"]["status"], "BLOCKED")

    def test_report_never_authorizes_release(self):
        self.assertFalse(self.report()["release_authorized"])
        self.assertEqual(self.report()["overall_status"], "BLOCKED")

    def test_valid_contract_is_offline_pass(self):
        self.assertEqual(self.report()["checks"]["schema_contract"]["status"], "PASS")

    def test_legacy_target_is_blocked(self):
        self.assertEqual(self.report()["checks"]["protected_destination"]["status"], "BLOCKED")

    def test_protected_target_still_requires_review(self):
        self.loader_path().write_text(
            LOADER.replace('BQ_TABLE = "KE24_staging"', 'BQ_TABLE = "KE24_staging_protected"'),
            encoding="utf-8",
        )
        self.assertEqual(self.report()["checks"]["protected_destination"]["status"], "REVIEW_REQUIRED")
        self.assertFalse(self.report()["release_authorized"])

    def test_approved_string_never_authorizes(self):
        self.policy["status"] = "APPROVED"
        self.save()
        self.assertEqual(self.report()["checks"]["privacy_policy"]["status"], "REVIEW_REQUIRED")
        self.assertFalse(self.report()["release_authorized"])

    def test_missing_policy_blocks(self):
        (self.root / "config" / "ke24_privacy_policy.json").unlink()
        self.assertEqual(self.report()["checks"]["privacy_policy"]["status"], "BLOCKED")

    def test_invalid_json_blocks(self):
        (self.root / "config" / "ke24_privacy_policy.json").write_text("{", encoding="utf-8")
        self.assertEqual(self.report()["checks"]["schema_contract"]["status"], "BLOCKED")

    def test_schema_version_mismatch_blocks(self):
        self.schema["schema_version"] = "9.9"
        self.save()
        self.assertEqual(self.report()["checks"]["schema_contract"]["status"], "BLOCKED")

    def test_missing_dimension_blocks(self):
        self.policy["tokenize_dimensions"] = ["company_code"]
        self.save()
        self.assertEqual(self.report()["checks"]["schema_contract"]["status"], "BLOCKED")

    def test_metadata_must_be_tokenized(self):
        self.policy["metadata_actions"]["_source_file"] = "keep"
        self.save()
        self.assertEqual(self.report()["checks"]["schema_contract"]["status"], "BLOCKED")

    def test_duplicate_schema_field_blocks(self):
        self.schema["columns"][4] = "customer"
        self.save()
        self.assertEqual(self.report()["checks"]["schema_contract"]["status"], "BLOCKED")

    def test_writer_guard_detected_statically(self):
        self.assertEqual(self.report()["checks"]["writer_guard_static"]["status"], "PASS")

    def test_removed_writer_guard_not_verified(self):
        self.loader_path().write_text(
            LOADER.replace('    raise RuntimeError("Gravação BigQuery desabilitada: operação ainda não implementada.")', '    return client.query("SELECT 1")'),
            encoding="utf-8",
        )
        self.assertEqual(self.report()["checks"]["writer_guard_static"]["status"], "NOT_VERIFIED")

    def test_missing_loader_fails_closed(self):
        self.loader_path().unlink()
        report = self.report()
        self.assertEqual(report["checks"]["writer_guard_static"]["status"], "NOT_VERIFIED")
        self.assertEqual(report["overall_status"], "BLOCKED")

    def test_infrastructure_is_never_claimed_verified(self):
        checks = self.report()["checks"]
        for item in ("gcp_permissions", "cloud_integration", "corporate_approval"):
            self.assertEqual(checks[item]["status"], "NOT_VERIFIED")

    def test_report_contains_no_sensitive_details(self):
        contents = json.dumps(self.report())
        for secret in ("SECRET", "PRIVATE_FILENAME.xlsx", "financial_measure_42"):
            self.assertNotIn(secret, contents)

    def test_real_repository_is_not_authorized(self):
        report = build_readiness_report()
        self.assertEqual(report["mode"], "OFFLINE_ONLY")
        self.assertFalse(report["release_authorized"])

    def test_readiness_does_not_call_cloud_or_hmac_key(self):
        with patch("socket.create_connection", side_effect=AssertionError("Rede não permitida")):
            report = self.report()
        self.assertFalse(report["release_authorized"])

if __name__ == "__main__":
    unittest.main()
