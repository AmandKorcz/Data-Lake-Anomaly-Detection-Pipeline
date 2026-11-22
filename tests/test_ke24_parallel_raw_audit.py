import hashlib
import threading
from io import BytesIO

import pytest

from src.pipeline.ke24_parallel_raw_audit import (
    DuplicateRawContent,
    RawAuditIOError,
    audit_raw_sources_parallel,
    resolve_worker_count,
)


SECRET = b"TEST_SECRET_ONLY_NOT_REAL_1234567890"


def fake_hash(buffer):
    return hashlib.sha256(buffer.read()).hexdigest()


def fake_tokenize(*, value, column, company, secret_key):
    assert column == "_source_file_sha256"
    assert company is None
    assert secret_key == SECRET
    return "TKN_" + hashlib.md5(value.encode("ascii")).hexdigest()


def execute(files, **kwargs):
    def download(service, file_id):
        return BytesIO(files[file_id])

    return audit_raw_sources_parallel(
        [{"id": file_id} for file_id in files],
        SECRET,
        service_factory=lambda: object(),
        download=download,
        calculate_hash=fake_hash,
        tokenize=fake_tokenize,
        retry_delay=0,
        **kwargs,
    )


def test_hashes_every_raw_file_and_uses_protected_tokens():
    files = {str(i): f"SYNTHETIC-XLSX-{i}".encode() for i in range(21)}
    tokens = execute(files, workers=3)
    assert len(tokens) == 21
    assert all(token.startswith("TKN_") and len(token) == 36 for token in tokens)
    assert tokens == {fake_tokenize(
        value=hashlib.sha256(data).hexdigest(),
        column="_source_file_sha256", company=None, secret_key=SECRET,
    ) for data in files.values()}


def test_duplicate_content_blocks_even_when_ids_are_distinct():
    with pytest.raises(DuplicateRawContent):
        execute({"remote1": b"SAME", "remote2": b"SAME"}, workers=2)


def test_repeated_remote_id_is_rejected_before_download():
    with pytest.raises(DuplicateRawContent):
        audit_raw_sources_parallel(
            [{"id": "same"}, {"id": "same"}], SECRET, workers=2,
            service_factory=lambda: pytest.fail("Nao conectar"),
        )


def test_one_worker_is_supported_and_reuses_its_own_service():
    files = {str(i): f"RAW{i}".encode() for i in range(4)}
    creations = []
    def factory():
        creations.append(object())
        return creations[-1]
    def download(service, file_id):
        assert service is creations[0]
        return BytesIO(files[file_id])
    tokens = audit_raw_sources_parallel(
        [{"id": file_id} for file_id in files], SECRET, workers=1,
        service_factory=factory, download=download,
        calculate_hash=fake_hash, tokenize=fake_tokenize,
    )
    assert len(tokens) == 4
    assert len(creations) == 1


def test_distinct_services_for_simultaneous_workers():
    files = {str(i): f"RAW{i}".encode() for i in range(3)}
    barrier = threading.Barrier(3)
    ids = {}
    lock = threading.Lock()
    def factory():
        service = object()
        with lock:
            ids[threading.get_ident()] = service
        return service
    def download(service, file_id):
        barrier.wait(timeout=5)
        with lock:
            assert ids[threading.get_ident()] is service
        return BytesIO(files[file_id])
    tokens = audit_raw_sources_parallel(
        [{"id": file_id} for file_id in files], SECRET, workers=3,
        service_factory=factory, download=download,
        calculate_hash=fake_hash, tokenize=fake_tokenize,
    )
    assert len(tokens) == len(ids) == 3
    assert len({id(value) for value in ids.values()}) == 3


def test_connection_reset_retries_with_fresh_client():
    attempts = []
    services = []
    def factory():
        service = object()
        services.append(service)
        return service
    def download(service, _file_id):
        attempts.append(service)
        if len(attempts) == 1:
            raise ConnectionResetError("SECRET_URL_NEVER_PRINT")
        return BytesIO(b"SAFE")
    result = audit_raw_sources_parallel(
        [{"id": "private_id"}], SECRET, workers=2,
        service_factory=factory, download=download,
        calculate_hash=fake_hash, tokenize=fake_tokenize,
        retry_delay=0,
    )
    assert len(result) == 1
    assert len(services) == 2
    assert services[0] is not services[1]


def test_permanent_connection_reset_is_sanitized():
    def download(*args):
        raise ConnectionResetError("SECRET_URL_NEVER_PRINT")
    with pytest.raises(RawAuditIOError) as exc:
        audit_raw_sources_parallel(
            [{"id": "private_id"}], SECRET, workers=1,
            service_factory=lambda: object(), download=download,
            calculate_hash=fake_hash, tokenize=fake_tokenize,
            retry_delay=0,
        )
    assert "SECRET_URL_NEVER_PRINT" not in str(exc.value)
    assert "private_id" not in str(exc.value)


def test_non_network_error_is_not_retried_and_message_is_sanitized():
    calls = []
    def download(*args):
        calls.append(1)
        raise ValueError("Private Drive ID here")
    with pytest.raises(RawAuditIOError) as exc:
        audit_raw_sources_parallel(
            [{"id": "private_id"}], SECRET, workers=1,
            service_factory=lambda: object(), download=download,
            calculate_hash=fake_hash, tokenize=fake_tokenize,
        )
    assert len(calls) == 1
    assert "Private Drive ID" not in str(exc.value)


def test_bad_token_is_rejected():
    with pytest.raises(RawAuditIOError):
        audit_raw_sources_parallel(
            [{"id": "a"}], SECRET, workers=1,
            service_factory=lambda: object(),
            download=lambda _service, _id: BytesIO(b"safe"),
            calculate_hash=fake_hash, tokenize=lambda **kwargs: "RAW-ID",
        )


@pytest.mark.parametrize("value", ["0", "5", "-1", "abc", "1.5", " 3 ", ""])
def test_invalid_env_worker_count_is_blocked(value):
    with pytest.raises(ValueError):
        resolve_worker_count({"KE24_RAW_AUDIT_WORKERS": value})


def test_environment_worker_count_defaults_to_three():
    assert resolve_worker_count({}) == 3
    assert resolve_worker_count({"KE24_RAW_AUDIT_WORKERS": "1"}) == 1
    assert resolve_worker_count({"KE24_RAW_AUDIT_WORKERS": "4"}) == 4


def test_no_remote_inputs_does_not_authenticate():
    with pytest.raises(ValueError):
        audit_raw_sources_parallel([], SECRET, service_factory=lambda: pytest.fail("Conexao indevida"))


def test_invalid_secret_is_blocked():
    with pytest.raises(ValueError):
        audit_raw_sources_parallel([{"id": "file"}], b"short", workers=2)
