from __future__ import annotations

import errno
import math
import time
from collections.abc import Callable

RETRYABLE_HTTP = frozenset({429, 500, 502, 503, 504})
RETRYABLE_ERRNO = frozenset({
    errno.ETIMEDOUT, errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE,
})


class DriveReadUnavailable(RuntimeError):
    """Falha transitoria esgotada, sem divulgar a excecao original."""


class DriveReadRejected(RuntimeError):
    """Resposta HTTP nao recuperavel, com identificadores omitidos."""


def _http_status(error: BaseException) -> int | None:
    response = getattr(error, "resp", None)
    raw = getattr(response, "status", None)
    if type(raw) is int and 100 <= raw <= 599:
        return raw
    if isinstance(raw, str) and raw.isdecimal() and 100 <= int(raw) <= 599:
        return int(raw)
    return None


def _is_temporary(error: BaseException) -> bool:
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    if isinstance(error, OSError) and error.errno in RETRYABLE_ERRNO:
        return True
    return _http_status(error) in RETRYABLE_HTTP


def execute_drive_read(
    request_factory: Callable,
    *,
    attempts: int = 3,
    first_delay_seconds: float = 0.35,
    sleeper: Callable = time.sleep,
):
    if not callable(request_factory) or not callable(sleeper):
        raise TypeError("Fabrica de leitura e temporizador precisam ser funcoes.")
    if type(attempts) is not int or not 1 <= attempts <= 5:
        raise ValueError("Quantidade de tentativas invalida (1 a 5).")
    if (type(first_delay_seconds) not in (int, float)
            or not math.isfinite(first_delay_seconds)
            or not 0 <= first_delay_seconds <= 5):
        raise ValueError("Intervalo de nova tentativa invalido.")

    for index in range(attempts):
        try:
            return request_factory().execute()
        except (Exception,) as exc:
            status = _http_status(exc)
            temporary = _is_temporary(exc)
            if not temporary:
                if status is not None:
                    raise DriveReadRejected(
                        f"Leitura do Drive rejeitada (HTTP {status}); nenhuma alteracao realizada."
                    ) from None
                raise
            if index == attempts - 1:
                raise DriveReadUnavailable(
                    "Leitura do Drive indisponivel apos tentativas limitadas; "
                    "nenhuma alteracao realizada."
                ) from None
            if first_delay_seconds:
                sleeper(first_delay_seconds * (2 ** index))

    raise AssertionError("Estado inesperado no controle de tentativas")
