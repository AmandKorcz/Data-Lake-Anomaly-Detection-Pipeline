import argparse
import math
import re

import pandas as pd


_TOKEN = re.compile(r"TKN_[0-9a-f]{32}\Z")
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_EXPERIMENTAL_STATUS = "experimental_auto_classification"


def _required_nonnegative_number(value):
    """Aceita numeros JSON finitos, nao strings, bool ou NaN/inf."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Limite numerico invalido na configuracao de regras.")
    if not math.isfinite(value) or value < 0:
        raise ValueError("Limite numerico invalido na configuracao de regras.")
    return float(value)


def _threshold_pair(section, medium_key, high_key, *, maximum=None):
    """Retorna quantos campos faltam; falha diante de valores malformados."""
    medium = section.get(medium_key)
    high = section.get(high_key)
    missing_count = int(medium is None) + int(high is None)

    # Um dos valores definido de forma invalida nao pode passar como pendente.
    for value in (medium, high):
        if value is not None:
            checked = _required_nonnegative_number(value)
            if maximum is not None and checked > maximum:
                raise ValueError("Limite tecnico fora da faixa permitida.")

    if missing_count:
        return missing_count
    if not float(medium) < float(high):
        raise ValueError("Limites de prioridade devem ser estritamente crescentes.")
    return 0


def _optional_legacy_pair(section, medium_key, high_key):
    """Compatibilidade: nao exige percentis legados no fluxo pretrained."""
    a, b = section.get(medium_key), section.get(high_key)
    if a is None and b is None:
        return
    if a is None or b is None:
        raise ValueError("Par de percentis legados configurado parcialmente.")
    _threshold_pair(section, medium_key, high_key, maximum=100)


def assess_protected_rules(rules, candidate_context=None):
    """Devolve somente status e contagens, nunca dados/limites das regras.

    ``candidate_context`` e opcional e deve conter apenas as colunas de
    empresa protegida, moeda e tipo de moeda. A ausencia desse contexto
    NAO significa que todas as empresas do Drive estejam cobertas.

    ``business_approved`` e sempre False: uma string de status no JSON
    nao prova aprovacao de FP&A ou de outro responsavel de negocio.
    """
    if not isinstance(rules, dict):
        raise ValueError("Regras protegidas invalidas.")

    basis = rules.get("currency_basis")
    if not isinstance(basis, dict) or type(basis.get("currency_type")) is not int:
        raise ValueError("Base de moeda nao configurada corretamente.")
    if basis["currency_type"] != 10:
        raise ValueError("Base de moeda diferente do contrato KE24 esperado.")

    companies = rules.get("business_thresholds_by_company")
    if not isinstance(companies, dict) or not companies:
        raise ValueError("Regras por empresa ausentes ou invalidas.")

    missing_company_thresholds = 0
    incomplete_companies = 0
    normalized_companies = {}

    for protected_company, thresholds in companies.items():
        if not isinstance(protected_company, str) or not _TOKEN.fullmatch(protected_company):
            raise ValueError("Identificador de empresa nao protegido nas regras.")
        if not isinstance(thresholds, dict):
            raise ValueError("Regras de empresa devem ser um objeto.")
        currency = thresholds.get("currency")
        if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency.strip().upper()):
            raise ValueError("Moeda da regra de empresa invalida.")
        missing = _threshold_pair(
            thresholds, "materiality_medium_min", "materiality_high_min"
        )
        missing_company_thresholds += missing
        incomplete_companies += int(missing > 0)
        normalized_companies[protected_company] = currency.strip().upper()

    technical = rules.get("technical_thresholds")
    if technical is None:
        technical = {}
    if not isinstance(technical, dict):
        raise ValueError("Regras tecnicas devem ser um objeto.")

    missing_technical_thresholds = sum((
        _threshold_pair(technical, "pretrained_margin_medium_min", "pretrained_margin_high_min"),
        _threshold_pair(technical, "stability_medium_min", "stability_high_min", maximum=100),
    ))
    _optional_legacy_pair(
        technical, "anomaly_percentile_medium_min", "anomaly_percentile_high_min"
    )

    scope_checked = candidate_context is not None
    scope_company_missing = 0
    scope_currency_mismatch = 0
    scope_unique_company_currencies = 0

    if scope_checked:
        if not isinstance(candidate_context, pd.DataFrame):
            raise ValueError("Contexto dos candidatos invalido.")
        columns = ("company_code", "currency", "currency_type")
        if candidate_context.columns.duplicated().any() or not set(columns).issubset(candidate_context):
            raise ValueError("Contexto protegido dos candidatos incompleto.")
        context = candidate_context.loc[:, list(columns)].copy()
        if context.isna().any().any():
            raise ValueError("Contexto dos candidatos contem valores ausentes.")
        if not context["company_code"].astype("string").str.fullmatch(
            _TOKEN.pattern
        ).fillna(False).all():
            raise ValueError("Contexto dos candidatos contem empresa nao protegida.")
        if not context["currency"].astype("string").str.strip().str.upper().str.fullmatch(
            _CURRENCY.pattern
        ).fillna(False).all():
            raise ValueError("Contexto dos candidatos contem moeda invalida.")
        types = pd.to_numeric(context["currency_type"], errors="coerce")
        if types.isna().any() or not types.eq(10).all():
            raise ValueError("Contexto dos candidatos usa base de moeda inesperada.")

        pairs = context.loc[:, ["company_code", "currency"]].copy()
        pairs["currency"] = pairs["currency"].astype("string").str.strip().str.upper()
        pairs = pairs.drop_duplicates()
        scope_unique_company_currencies = len(pairs)
        for company, currency in pairs.itertuples(index=False, name=None):
            if company not in normalized_companies:
                scope_company_missing += 1
            elif normalized_companies[company] != currency:
                scope_currency_mismatch += 1

    incomplete = bool(
        missing_company_thresholds or missing_technical_thresholds
        or scope_company_missing or scope_currency_mismatch
    )
    raw_status = rules.get("status")
    if raw_status is not None and not isinstance(raw_status, str):
        raise ValueError("Status da configuracao invalido.")

    if incomplete:
        state = "INCOMPLETE"
    elif raw_status == _EXPERIMENTAL_STATUS:
        state = "EXPERIMENTAL_REVIEW"
    else:
        state = "PENDING_BUSINESS_APPROVAL"

    return {
        "status": state,
        "company_rules": len(companies),
        "company_rules_incomplete": incomplete_companies,
        "missing_company_thresholds": missing_company_thresholds,
        "missing_technical_thresholds": missing_technical_thresholds,
        "technical_complete": missing_technical_thresholds == 0,
        "scope_checked": scope_checked,
        "scope_unique_company_currencies": scope_unique_company_currencies,
        "scope_company_missing": scope_company_missing,
        "scope_currency_mismatch": scope_currency_mismatch,
        "business_approved": False,
        "publication_authorized": False,
    }


def main(argv=None):
    """Audita regras privadas locais, sem conectar ao Drive nem publicar."""
    parser = argparse.ArgumentParser(description="KE24: auditoria offline de regras financeiras")
    parser.add_argument(
        "--check-private", action="store_true",
        help="Valida configuracao privada externa, sem imprimir limites/empresas",
    )
    args = parser.parse_args(argv)
    if not args.check_private:
        parser.print_help()
        return 0

    # Import tardio: --help nunca exige chave HMAC, arquivo externo ou OAuth.
    try:
        from src.ml.load_ke24_alert_rules import load_protected_alert_rules
        report = assess_protected_rules(load_protected_alert_rules())
    except Exception:
        # Erros externos podem conter caminhos, IDs ou regras; nao os exibimos.
        print("BLOCKED: nao foi possivel validar as regras privadas.")
        return 1

    print("KE24 - Auditoria offline de regras (sem Drive/Sheets/publicacao)")
    for key in (
        "status", "company_rules", "company_rules_incomplete",
        "missing_company_thresholds", "missing_technical_thresholds",
        "scope_checked", "business_approved", "publication_authorized",
    ):
        print(f"{key}: {report[key]}")
    print("Aprovacao de negocio nao e inferida do JSON; classificacao somente diagnostica.")
    return 2 if report["status"] == "INCOMPLETE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
