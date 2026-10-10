import unittest

import pandas as pd

from src.loading.plan_ke24_incremental import (
    build_file_manifest,
    plan_incremental_load,
)


def fake_file(
    file_char,
    company_char,
    period,
    count=2,
    currency="RUB",
):
    # Cria registros fictícios com tokens simulados
    rows = []

    for index in range(count):
        rows.append({
            "_source_file_sha256": "TKN_" + file_char * 32,
            "_source_file": "TKN_" + file_char * 32,
            "_source_row_number": index + 2,
            "company_code": "TKN_" + company_char * 32,
            "period": int(period.split(".")[0]),
            "period_year": period,
            "currency": currency,
        })

    return pd.DataFrame(rows)

class TestKE24IncrementalPlan(unittest.TestCase):

    def setUp(self):
        self.january = fake_file(
            "a", "c", "01.2026"
        )

        self.february = fake_file(
            "b", "c", "02.2026",
            count=1,
        )

    def test_first_load(self):
        batch = pd.concat(
            [self.january, self.february],
            ignore_index=True,
        )

        plan = plan_incremental_load(
            batch,
            pd.DataFrame(),
        )

        self.assertEqual(plan["new_files"], 2)
        self.assertEqual(plan["skipped_files"], 0)
        self.assertEqual(len(plan["new_rows"]), 3)

    def test_existing_file_is_skipped(self):
        batch = pd.concat(
            [self.january, self.february],
            ignore_index=True,
        )

        loaded = build_file_manifest(
            self.january
        )

        plan = plan_incremental_load(
            batch,
            loaded,
        )

        self.assertEqual(plan["new_files"], 1)
        self.assertEqual(plan["skipped_files"], 1)
        self.assertEqual(len(plan["new_rows"]), 1)

    def test_monthly_replacement_is_blocked(self):
        corrected_january = fake_file(
            "z", "c", "01.2026"
        )

        loaded = build_file_manifest(
            self.january
        )

        with self.assertRaises(ValueError):
            plan_incremental_load(
                corrected_january,
                loaded,
            )

    def test_same_hash_with_other_company_is_blocked(self):
        inconsistent_file = fake_file(
            "a", "d", "01.2026"
        )

        loaded = build_file_manifest(
            self.january
        )

        with self.assertRaises(ValueError):
            plan_incremental_load(
                inconsistent_file,
                loaded,
            )

    def test_partial_load_is_blocked(self):
        loaded = build_file_manifest(
            self.january
        )

        loaded.loc[0, "record_count"] = 1

        with self.assertRaises(ValueError):
            plan_incremental_load(
                self.january,
                loaded,
            )

    def test_mixed_companies_are_blocked(self):
        inconsistent_file = self.january.copy()

        inconsistent_file.loc[
            0, "company_code"
        ] = "TKN_" + "d" * 32

        with self.assertRaises(ValueError):
            build_file_manifest(
                inconsistent_file
            )

    def test_duplicate_monthly_files_are_blocked(self):
        duplicate_month = fake_file(
            "b", "c", "01.2026"
        )

        batch = pd.concat(
            [self.january, duplicate_month],
            ignore_index=True,
        )

        with self.assertRaises(ValueError):
            build_file_manifest(batch)

    def test_different_months_with_same_year(self):
        january = fake_file("a", "c", "01.2026")
        february = fake_file("b", "c", "02.2026")

        january["period_year"] = "2026"
        february["period_year"] = "2026"

        batch = pd.concat(
            [january, february],
            ignore_index=True,
        )

        manifest = build_file_manifest(batch)

        self.assertEqual(len(manifest), 2)
        self.assertEqual(
            set(manifest["period"]),
            {1, 2},
        )

if __name__ == "__main__":
    unittest.main()
