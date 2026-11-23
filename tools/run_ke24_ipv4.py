from __future__ import annotations

import argparse
import runpy
import sys

from tools.ke24_google_ipv4_compat import google_api_ipv4_only

ALLOWED_MODULES = (
    "tools.ke24_new_company_preflight",
    "src.pipeline.run_ke24_drive_ingestion",
    "src.pipeline.run_ke24",
    "src.pipeline.run_ke24_drive_model",
    "src.pipeline.run_ke24_drive_alert_persistence",
    "src.review.run_ke24_review_sheet",
    "tools.ke24_review_context_tab",
    "tools.diagnose_ke24_recurrence",
    "src.pipeline.run_ke24_bigquery_processed",
)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Executa um comando KE24 isolado priorizando IPv4 na API Google."
    )
    parser.add_argument("module", choices=ALLOWED_MODULES)
    parsed, forwarded = parser.parse_known_args(argv)
    previous_argv = sys.argv
    try:
        sys.argv = [parsed.module, *forwarded]
        with google_api_ipv4_only():
            runpy.run_module(parsed.module, run_name="__main__", alter_sys=False)
    finally:
        sys.argv = previous_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
