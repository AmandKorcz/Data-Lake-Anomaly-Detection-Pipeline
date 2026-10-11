#Testes offline do checklist de evidências para homologação KE24

import unittest
from copy import deepcopy
from io import StringIO
from unittest.mock import patch

from src.loading.ke24_homologation_checklist import (
    CONTROLS,
    build_homologation_checklist,
    main,
    render_checklist_markdown,
)

def fake_readiness():
    return {
        "mode": "OFFLINE_ONLY",
        "overall_status": "BLOCKED",
        "release_authorized": False,
        "checks": {
            "privacy_policy": {"status": "BLOCKED", "detail": "NOT_PUBLIC"},
            "schema_contract": {"status": "PASS"},
            "protected_destination": {"status": "BLOCKED"},
            "writer_guard_static": {"status": "PASS"},
            "gcp_permissions": {"status": "NOT_VERIFIED"},
            "cloud_integration": {"status": "NOT_VERIFIED"},
            "corporate_approval": {"status": "NOT_VERIFIED"},
        },
    }

class TestKE24HomologationChecklist(unittest.TestCase):
    def checklist(self, source=None):
        with patch(
            "src.loading.ke24_homologation_checklist.build_readiness_report",
            return_value=fake_readiness() if source is None else source,
        ):
            return build_homologation_checklist()

    def by_id(self, checklist):
        return {item["id"]: item for item in checklist["items"]}

    def test_explicit_offline_mode(self):
        self.assertEqual(self.checklist()["mode"], "OFFLINE_ONLY")

    def test_never_authorizes_release(self):
        result = self.checklist()
        self.assertEqual(result["overall_status"], "BLOCKED")
        self.assertIs(result["release_authorized"], False)

    def test_checklist_has_stable_unique_controls(self):
        ids = [x["id"] for x in self.checklist()["items"]]
        self.assertEqual(ids, [x[0] for x in CONTROLS])
        self.assertEqual(len(ids), len(set(ids)))

    def test_schema_offline_pass(self):
        self.assertEqual(self.by_id(self.checklist())["schema_contract"]["status"], "OFFLINE_PASS")

    def test_writer_guard_offline_pass(self):
        self.assertEqual(self.by_id(self.checklist())["write_disabled"]["status"], "OFFLINE_PASS")

    def test_draft_policy_is_blocked(self):
        self.assertEqual(self.by_id(self.checklist())["privacy_policy"]["status"], "BLOCKED")

    def test_legacy_target_is_blocked(self):
        self.assertEqual(self.by_id(self.checklist())["protected_destination"]["status"], "BLOCKED")

    def test_external_evidence_never_claimed_verified(self):
        by_id = self.by_id(self.checklist())
        for code in (
            "iam_least_privilege", "gcs_metadata_controls", "bigquery_job_identity",
            "exact_batch_evidence", "hmac_key_custody", "synthetic_cloud_trial",
            "failure_recovery", "corporate_approval",
        ):
            with self.subTest(code=code):
                self.assertEqual(by_id[code]["status"], "NOT_VERIFIED")

    def test_summary_matches_items(self):
        result = self.checklist()
        self.assertEqual(result["summary"], {
            "offline_pass": 2, "blocked": 2, "not_verified": 8,
        })

    def test_report_claiming_approved_never_enables_release(self):
        source = fake_readiness()
        source["checks"]["privacy_policy"]["status"] = "PASS"
        source["release_authorized"] = True
        source["overall_status"] = "READY"
        result = self.checklist(source)
        self.assertEqual(self.by_id(result)["privacy_policy"]["status"], "NOT_VERIFIED")
        self.assertFalse(result["release_authorized"])

    def test_protected_target_still_needs_review(self):
        source = fake_readiness()
        source["checks"]["protected_destination"]["status"] = "REVIEW_REQUIRED"
        self.assertEqual(
            self.by_id(self.checklist(source))["protected_destination"]["status"],
            "NOT_VERIFIED",
        )

    def test_external_status_pass_cannot_be_spoofed(self):
        source = fake_readiness()
        source["checks"]["gcp_permissions"]["status"] = "PASS"
        source["checks"]["corporate_approval"]["status"] = "PASS"
        result = self.checklist(source)
        self.assertEqual(self.by_id(result)["iam_least_privilege"]["status"], "NOT_VERIFIED")
        self.assertEqual(self.by_id(result)["corporate_approval"]["status"], "NOT_VERIFIED")

    def test_missing_or_invalid_report_fails_closed(self):
        for source in (None, [], {"checks": []}, {"checks": {"schema_contract": {"status": []}}}, {"checks": {"schema_contract": {"status": "SECRET"}}}):
            with self.subTest(source=str(type(source))):
                with patch(
                    "src.loading.ke24_homologation_checklist.build_readiness_report",
                    return_value=source,
                ):
                    result = build_homologation_checklist()
                self.assertEqual(result["summary"]["offline_pass"], 0)
                self.assertFalse(result["release_authorized"])

    def test_readiness_exception_fails_closed(self):
        with patch(
            "src.loading.ke24_homologation_checklist.build_readiness_report",
            side_effect=RuntimeError("SENSITIVE_TOKEN"),
        ):
            result = build_homologation_checklist()
        self.assertEqual(result["summary"]["offline_pass"], 0)
        self.assertNotIn("SENSITIVE_TOKEN", str(result))

    def test_blocked_schema_is_not_pass(self):
        source = fake_readiness()
        source["checks"]["schema_contract"]["status"] = "BLOCKED"
        self.assertEqual(self.by_id(self.checklist(source))["schema_contract"]["status"], "BLOCKED")

    def test_unknown_guard_is_not_pass(self):
        source = fake_readiness()
        source["checks"]["writer_guard_static"]["status"] = "NOT_VERIFIED"
        self.assertEqual(self.by_id(self.checklist(source))["write_disabled"]["status"], "NOT_VERIFIED")

    def test_evidence_descriptions_and_roles_are_present(self):
        for item in self.checklist()["items"]:
            self.assertTrue(item["responsible_role"])
            self.assertTrue(item["required_evidence"])

    def test_markdown_is_safe_and_informative(self):
        source = fake_readiness()
        source["checks"]["schema_contract"]["detail"] = "PRIVATE_FINANCIAL_VALUE"
        text = render_checklist_markdown(self.checklist(source))
        self.assertIn("OFFLINE_PASS", text)
        self.assertIn("NOT_VERIFIED", text)
        self.assertIn("BLOCKED", text)
        self.assertIn("Custódia da chave HMAC", text)
        self.assertNotIn("PRIVATE_FINANCIAL_VALUE", text)
        self.assertNotIn("NOT_PUBLIC", text)

    def test_markdown_rejects_forged_approval(self):
        result = self.checklist()
        result["release_authorized"] = True
        with self.assertRaises(ValueError):
            render_checklist_markdown(result)

    def test_markdown_rejects_external_offline_pass(self):
        result = deepcopy(self.checklist())
        self.by_id(result)["corporate_approval"]["status"] = "OFFLINE_PASS"
        with self.assertRaises(ValueError):
            render_checklist_markdown(result)

    def test_main_prints_only_checklist(self):
        with (
            patch("src.loading.ke24_homologation_checklist.build_readiness_report", return_value=fake_readiness()),
            patch("sys.stdout", new_callable=StringIO) as stdout,
        ):
            main()
        output = stdout.getvalue()
        self.assertIn("nenhuma liberação operacional autorizada", output)
        self.assertNotIn("NOT_PUBLIC", output)


if __name__ == "__main__":
    unittest.main()
