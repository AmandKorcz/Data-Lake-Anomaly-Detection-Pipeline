import argparse

import pandas as pd

from src.business_rules.ke24_business_checks import (
    evaluate_business_checks, load_policy, summarize_business_checks,
)


def _print_summary(results):
    totals = summarize_business_checks(results)
    print("KE24 - Regras oficiais de negocio v1 (sem publicacao)")
    print("Total de checagens:", len(results))
    for status, count in totals.items():
        print(f"{status}: {count}")
    print("Alertas de revisao NAO sao erros financeiros comprovados.")


def _demo():
    # Tokens e valores exclusivamente sinteticos.
    company = "TKN_" + "a" * 32
    rows = []
    for period, revenue, material in (
        ("2026-01", 100.0, -20.0),
        ("2026-02", 102.0, -19.0),
        ("2026-03", 98.0, -21.0),
        ("2026-04", 190.0, -50.0),
    ):
        rows.append({"company_code": company, "currency_type": 10,
                     "currency": "MXN", "period_key": period, "revenue": revenue,
                     "c_4rm_copper_wire": material})
    rows.append({"company_code": company, "currency_type": 10,
                 "currency": "USD", "period_key": "2026-04", "revenue": 20.0,
                 "c_4rm_copper_wire": -2.0})
    return pd.DataFrame(rows), {company: ["MXN"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="KE24: checks oficiais de negocio v1")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-config", action="store_true", help="Validacao offline do JSON")
    mode.add_argument("--demo", action="store_true", help="Exemplo sintetico, offline")
    mode.add_argument("--preview", action="store_true", help="Read-only no processed do Google Drive")
    args = parser.parse_args(argv)
    policy = load_policy()
    enabled = [name for name, spec in policy["metrics"].items() if spec["enabled"]]
    if args.check_config:
        print("Politica v1: ACTIVE | metricas ativas:", len(enabled))
        print("Nenhuma conexao nem escrita realizada.")
        return 0
    if args.demo:
        df, currencies = _demo()
        _print_summary(evaluate_business_checks(df, policy, company_currencies=currencies))
        return 0

    # Importar somente quando o usuario solicita acesso explicito ao Drive.
    from googleapiclient.discovery import build
    from src.config.ke24_local_settings import load_ke24_local_settings
    from src.ingestion.google_drive_client import authenticate
    from src.pipeline.run_ke24_drive_features import load_verified_processed_dataframe
    from src.privacy.privacy_runtime import load_privacy_runtime
    from src.business_rules.ke24_company_currency import load_company_currency_rules
    from src.pipeline.run_ke24 import audit_drive_sources

    load_ke24_local_settings()
    privacy_policy, schema, secret_key = load_privacy_runtime()
    service = build("drive", "v3", credentials=authenticate(), cache_discovery=False)
    audit_drive_sources(service, secret_key)
    processed = load_verified_processed_dataframe(service, privacy_policy, schema)
    company_currencies = load_company_currency_rules(secret_key)
    results = evaluate_business_checks(processed, policy, company_currencies=company_currencies)
    _print_summary(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
