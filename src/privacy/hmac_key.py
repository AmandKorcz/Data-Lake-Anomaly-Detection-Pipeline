import os 
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

KEY_ENV_VARIABLE = "KE24_HMAC_KEY_FILE"
EXPECTED_KEY_BYTES = 32

def load_hmac_key_from_environment():
    #Carrega uma chave HMAC de um arquivo externo
    
    key_location = os.environ.get(KEY_ENV_VARIABLE)

    if not key_location:
        raise RuntimeError(
            f"Variável {KEY_ENV_VARIABLE} não configurada."
        )

    key_path = Path(key_location).expanduser().resolve()

    #Impede o uso de uma chave dentro do repositório
    if key_path.is_relative_to(PROJECT_ROOT.resolve()):
        raise ValueError("A chave HMAC deve ficar fora do repositório")

    if not key_path.is_file():
        raise FileNotFoundError("Arquivo da chave HMAC não encontrado.")

    try:
        key_content = key_path.read_text(
            encoding="ascii"
        ).strip()
    except (OSError, UnicodeError) as error:
        raise RuntimeError(
            "Não foi posível ler a chave HMAC"
        ) from error

    if len(key_content) != 64:
        raise ValueError(
            "A chave HMAC deve conter 64 caraceres hexadecimais."
        )

    try:
        secret_key = bytes.fromhex(key_content)
    except ValueError as error:
        raise ValueError(
            "O arquivo contém caracteres inválidos"
        ) from error

    if len(secret_key) != EXPECTED_KEY_BYTES:
        raise ValueError(
            "A chave HMAC deve possuir exatamente 32 bytes."
        )

    return secret_key