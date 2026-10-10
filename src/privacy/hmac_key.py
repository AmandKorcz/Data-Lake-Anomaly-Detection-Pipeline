import os
from pathlib import Path

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]

KEY_ENV_VARIABLE = "KE24_HMAC_KEY_FILE"
EXPECTED_KEY_BYTES = 32

def get_key_location():
    #Obtém a localização da chave HMAC.

    if KEY_ENV_VARIABLE in os.environ:
        key_location = os.environ[KEY_ENV_VARIABLE]
    else:
        configuration = dotenv_values(PROJECT_ROOT / ".env")
        key_location = configuration.get(KEY_ENV_VARIABLE)

    if not key_location or not key_location.strip():
        raise RuntimeError(
            "Local da chave HMAC não configurado."
        )

    return key_location


def load_hmac_key_from_environment():

    key_location = get_key_location()

    # Expande variáveis como %LOCALAPPDATA% no Windows
    expanded_location = os.path.expandvars(key_location)

    key_path = (
        Path(expanded_location)
        .expanduser()
        .resolve()
    )

    # Impede arquivos de chave dentro do repositório
    if key_path.is_relative_to(PROJECT_ROOT.resolve()):
        raise ValueError(
            "A chave HMAC deve ficar fora do repositório."
        )

    if not key_path.is_file():
        raise FileNotFoundError(
            "Arquivo da chave HMAC não encontrado."
        )

    try:
        key_content = key_path.read_text(
            encoding="ascii"
        ).strip()

    except (OSError, UnicodeError) as error:
        raise RuntimeError(
            "Não foi possível ler a chave HMAC."
        ) from error

    if len(key_content) != 64:
        raise ValueError(
            "A chave HMAC deve conter 64 caracteres hexadecimais."
        )

    try:
        secret_key = bytes.fromhex(key_content)

    except ValueError as error:
        raise ValueError(
            "O arquivo contém caracteres inválidos."
        ) from error

    if len(secret_key) != EXPECTED_KEY_BYTES:
        raise ValueError(
            "A chave HMAC deve possuir exatamente 32 bytes."
        )

    return secret_key
