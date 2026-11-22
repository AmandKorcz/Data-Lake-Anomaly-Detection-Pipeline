from __future__ import annotations

from contextlib import contextmanager
import socket


def is_google_api_host(host: object) -> bool:
    return isinstance(host, str) and (
        host == "googleapis.com" or host.endswith(".googleapis.com")
    )


def ipv4_only_google_resolver(original):
    if not callable(original):
        raise TypeError("A resolucao DNS original precisa ser uma funcao.")

    def resolve(host, port, *args, **kwargs):
        addresses = original(host, port, *args, **kwargs)
        if not is_google_api_host(host):
            return addresses
        if not addresses:
            return addresses
        ipv4 = [address for address in addresses if address[0] == socket.AF_INET]
        if not ipv4:
            raise OSError("Nenhum endereco IPv4 disponivel para a API Google.")
        return ipv4

    return resolve


@contextmanager
def google_api_ipv4_only():
    original = socket.getaddrinfo
    socket.getaddrinfo = ipv4_only_google_resolver(original)
    try:
        yield
    finally:
        socket.getaddrinfo = original
