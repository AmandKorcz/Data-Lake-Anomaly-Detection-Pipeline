from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


TOKEN_PATTERN = re.compile(r"TKN_[0-9a-f]{32}\Z")
DEFAULT_WORKERS = 3
MAX_WORKERS = 4


class DuplicateRawContent(ValueError):
    """Duas entradas raw possuem conteudo identico."""


class RawAuditIOError(RuntimeError):
    """Falha de integridade/transferencia com mensagem sem identificadores."""


def resolve_worker_count(environ=None):
    env = os.environ if environ is None else environ
    value = env.get("KE24_RAW_AUDIT_WORKERS", str(DEFAULT_WORKERS))
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError("KE24_RAW_AUDIT_WORKERS deve ser um numero entre 1 e 4.")
    result = int(value)
    if not 1 <= result <= MAX_WORKERS:
        raise ValueError("KE24_RAW_AUDIT_WORKERS deve ser um numero entre 1 e 4.")
    return result


def _new_readonly_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    from src.ingestion.google_drive_client import SCOPES, TOKEN_FILE

    if not TOKEN_FILE.is_file():
        raise RawAuditIOError("Token de leitura do Drive indisponivel.")
    credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials.valid or not credentials.has_scopes(SCOPES):
        raise RawAuditIOError("Autorizacao somente leitura do Drive indisponivel.")
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def audit_raw_sources_parallel(
    source_files,
    secret_key,
    *,
    workers=None,
    service_factory=None,
    download=None,
    calculate_hash=None,
    tokenize=None,
    retry_delay=0.25,
):
    if not isinstance(secret_key, bytes) or len(secret_key) < 32:
        raise ValueError("Chave HMAC invalida para auditoria raw.")
    count = resolve_worker_count() if workers is None else workers
    if type(count) is not int or not 1 <= count <= MAX_WORKERS:
        raise ValueError("Quantidade de workers invalida (1 a 4).")
    if type(retry_delay) not in (int, float) or not 0 <= retry_delay <= 5:
        raise ValueError("Intervalo de retry invalido.")
    if not isinstance(source_files, list) or not source_files:
        raise ValueError("Pasta raw sem arquivos para auditoria.")

    source_ids = []
    for entry in source_files:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise ValueError("Metadados raw invalidos.")
        source_ids.append(entry["id"])
    if len(set(source_ids)) != len(source_ids):
        raise DuplicateRawContent("Arquivos raw duplicados; revisao necessaria.")

    if service_factory is None:
        service_factory = _new_readonly_service
    if download is None:
        from src.ingestion.google_drive_client import read_xlsx_from_drive
        download = read_xlsx_from_drive
    if calculate_hash is None:
        from src.ingestion.prepare_ke24 import calculate_file_hash as original_hash
        calculate_hash = original_hash
    if tokenize is None:
        from src.privacy.anonymize_ke24 import generate_token
        tokenize = generate_token

    session = threading.local()

    def audit_one(source_id):
        for attempt in range(2):
            try:
                if not hasattr(session, "service"):
                    session.service = service_factory()
                with download(session.service, source_id) as buffer:
                    digest = calculate_hash(buffer)
                protected = tokenize(
                    value=digest,
                    column="_source_file_sha256",
                    company=None,
                    secret_key=secret_key,
                )
                if not isinstance(protected, str) or not TOKEN_PATTERN.fullmatch(protected):
                    raise ValueError("HMAC de origem invalido.")
                return protected
            except (TimeoutError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                if hasattr(session, "service"):
                    del session.service
                if attempt == 0:
                    if retry_delay:
                        time.sleep(retry_delay)
                    continue
                raise RawAuditIOError("Falha de rede na conferencia raw. Nenhum dado alterado.") from None
            except Exception:
                raise RawAuditIOError("Falha ao verificar integridade da origem raw.") from None
        raise RawAuditIOError("Conferencia raw nao concluida.")

    protected_hashes = set()
    with ThreadPoolExecutor(max_workers=min(count, len(source_ids)), thread_name_prefix="ke24-raw") as pool:
        future_list = [pool.submit(audit_one, file_id) for file_id in source_ids]
        for future in as_completed(future_list):
            protected = future.result()
            if protected in protected_hashes:
                raise DuplicateRawContent("Conteudo XLSX duplicado no raw; revisao necessaria.")
            protected_hashes.add(protected)

    if len(protected_hashes) != len(source_ids):
        raise RawAuditIOError("Conferencia raw incompleta.")
    return protected_hashes
