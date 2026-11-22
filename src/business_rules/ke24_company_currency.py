import json
import os
from pathlib import Path

from dotenv import dotenv_values

from src.privacy.anonymize_ke24 import generate_token

ROOT = Path(__file__).resolve().parents[2]
ENV_NAME = "KE24_COMPANY_CURRENCY_RULES_FILE"


def load_company_currency_rules(secret_key, *, path=None):
    if path is None:
        location = os.environ.get(ENV_NAME)
        if location is None:
            location = dotenv_values(ROOT / "config" / ".env").get(ENV_NAME)
        if not location:
            return {}  # PENDING no motor, nao considerar uma moeda automaticamente aceita.
        path = location
    resolved = Path(os.path.expandvars(str(path))).expanduser().resolve()
    if resolved.is_relative_to(ROOT.resolve()):
        raise ValueError("Regras de empresas e moedas precisam ficar fora do repositorio.")
    with resolved.open(encoding="utf-8") as file:
        document = json.load(file)
    if not isinstance(document, dict) or not isinstance(document.get("company_currency"), dict):
        raise ValueError("Estrutura privada de moedas invalida.")
    protected = {}
    for company, currencies in document["company_currency"].items():
        if not isinstance(company, str) or not company.strip():
            raise ValueError("Codigo da empresa invalido.")
        if not isinstance(currencies, list) or not currencies:
            raise ValueError("Lista de moedas invalida.")
        token = generate_token(company, "company_code", company, secret_key)
        if token in protected:
            raise ValueError("Empresas duplicadas apos protecao.")
        protected[token] = currencies
    return protected
