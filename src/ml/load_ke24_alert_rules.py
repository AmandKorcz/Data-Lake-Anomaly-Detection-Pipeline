import json
import os
from pathlib import Path

from dotenv import dotenv_values

from src.ml.protect_ke24_alert_rules import protect_company_rule_keys
from src.privacy.privacy_runtime import load_privacy_runtime

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RULES_ENV_VARIABLE = "KE24_ALERT_RULES_FILE"

def resolve_private_rules_path():

    # Busca a configuração no ambiente ou no .env local
    location = os.environ.get(RULES_ENV_VARIABLE)

    if location is None:
        configuration = dotenv_values(PROJECT_ROOT / ".env")
        location = configuration.get(RULES_ENV_VARIABLE)

    if not isinstance(location, str) or not location.strip():
        raise RuntimeError(
            "Local das regras financeiras privadas não configurado."
        )

    rules_path = Path(
        os.path.expandvars(location.strip())
    ).expanduser().resolve()

    # Impede utilização de regras privadas dentro do repositório
    if rules_path.is_relative_to(PROJECT_ROOT.resolve()):
        raise ValueError(
            "O arquivo privado de regras deve ficar fora do repositório."
        )

    if not rules_path.is_file():
        raise FileNotFoundError(
            "Arquivo privado de regras financeiras não encontrado."
        )

    return rules_path

def load_protected_alert_rules():
    
    # Exige política aprovada e carrega a chave HMAC autorizada
    _, _, secret_key = load_privacy_runtime()

    rules_path = resolve_private_rules_path()

    try:
        with rules_path.open("r", encoding="utf-8") as file:
            private_rules = json.load(file)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError(
            "Não foi possível carregar um JSON válido de regras financeiras."
        ) from None

    # Transforma os identificadores somente em memória
    return protect_company_rule_keys(
        private_rules,
        secret_key,
    )
