import socket

import pytest

from tools.ke24_google_ipv4_compat import (
    google_api_ipv4_only,
    ipv4_only_google_resolver,
    is_google_api_host,
)
from tools.run_ke24_ipv4 import ALLOWED_MODULES


@pytest.mark.parametrize("host, expected", [
    ("www.googleapis.com", True),
    ("oauth2.googleapis.com", True),
    ("sheets.googleapis.com", True),
    ("googleapis.com", True),
    ("googleapis.com.evil.test", False),
    ("notgoogleapis.com", False),
    ("www.google.com", False),
    (None, False),
])
def test_google_api_hostname_scope(host, expected):
    assert is_google_api_host(host) is expected


def test_only_google_api_results_are_filtered():
    addresses = [(socket.AF_INET6, 1, 2, 3, ("::1", 443)),
                 (socket.AF_INET, 1, 2, 3, ("127.0.0.1", 443))]
    def original(host, port, *args, **kwargs):
        return addresses
    resolver = ipv4_only_google_resolver(original)
    assert resolver("www.googleapis.com", 443) == addresses[1:]
    assert resolver("example.com", 443) == addresses


def test_google_api_without_ipv4_fails_closed():
    resolver = ipv4_only_google_resolver(
        lambda host, port: [(socket.AF_INET6, 1, 2, 3, ("::1", 443))]
    )
    with pytest.raises(OSError, match="Nenhum endereco IPv4"):
        resolver("www.googleapis.com", 443)


def test_resolver_is_restored_even_on_error():
    original = socket.getaddrinfo
    with pytest.raises(RuntimeError):
        with google_api_ipv4_only():
            assert socket.getaddrinfo is not original
            raise RuntimeError("test exception")
    assert socket.getaddrinfo is original


def test_only_known_modules_are_allowed():
    assert "tools.ke24_new_company_preflight" in ALLOWED_MODULES
    assert "os" not in ALLOWED_MODULES
