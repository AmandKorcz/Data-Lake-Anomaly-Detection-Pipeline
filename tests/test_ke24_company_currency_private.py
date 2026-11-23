import json

import pytest

from src.business_rules.ke24_company_currency import load_company_currency_rules
from src.privacy.anonymize_ke24 import generate_token

KEY = b"KEY_ONLY_FOR_SYNTHETIC_TESTS_2026_12345"


def test_no_private_file_returns_empty_mapping(monkeypatch):
    monkeypatch.delenv("KE24_COMPANY_CURRENCY_RULES_FILE", raising=False)
    # Evita depender do .env pessoal ao executar o teste.
    monkeypatch.setattr("src.business_rules.ke24_company_currency.dotenv_values", lambda _: {})
    assert load_company_currency_rules(KEY) == {}


def test_private_file_protects_company_in_memory(tmp_path):
    path = tmp_path / "currency-private.json"
    path.write_text(json.dumps({"company_currency": {"MX_TEST_01": ["MXN"]}}), encoding="utf-8")
    mapping = load_company_currency_rules(KEY, path=path)
    token = generate_token("MX_TEST_01", "company_code", "MX_TEST_01", KEY)
    assert mapping == {token: ["MXN"]}
    assert "MX_TEST_01" not in repr(mapping)


def test_rejects_unexpected_private_schema(tmp_path):
    path = tmp_path / "currency-private.json"
    path.write_text(json.dumps({"companies": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="Estrutura privada"):
        load_company_currency_rules(KEY, path=path)


def test_rejects_private_file_inside_repo(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"company_currency": {"MX_TEST": ["MXN"]}}), encoding="utf-8")
    monkeypatch.setattr("src.business_rules.ke24_company_currency.ROOT", tmp_path)
    with pytest.raises(ValueError, match="fora do repositorio"):
        load_company_currency_rules(KEY, path=path)
