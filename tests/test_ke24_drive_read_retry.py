from types import SimpleNamespace

import pytest

from src.pipeline.ke24_drive_read_retry import (
    DriveReadRejected,
    DriveReadUnavailable,
    execute_drive_read,
)


class Request:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def execute(self):
        if self.error is not None:
            raise self.error
        return self.result


class FakeHttpError(Exception):
    def __init__(self, status, *, message="private-id-or-url"):
        self.resp = SimpleNamespace(status=status)
        super().__init__(message)


def factory(sequence, calls):
    stream = iter(sequence)
    def make():
        calls.append("new_request")
        value = next(stream)
        if isinstance(value, Exception):
            return Request(error=value)
        return Request(result=value)
    return make


def test_success_does_not_retry_or_sleep():
    called, delays = [], []
    result = execute_drive_read(factory([{"files": []}], called), sleeper=delays.append)
    assert result == {"files": []}
    assert called == ["new_request"]
    assert delays == []


def test_timeout_retries_with_new_request():
    calls, delays = [], []
    result = execute_drive_read(factory([TimeoutError("SECRET_ID"), b"payload"], calls),
                                first_delay_seconds=0.1, sleeper=delays.append)
    assert result == b"payload"
    assert calls == ["new_request", "new_request"]
    assert delays == [0.1]


def test_connection_reset_and_broken_pipe_retry():
    calls = []
    assert execute_drive_read(
        factory([ConnectionResetError("id"), BrokenPipeError("id"), 42], calls),
        first_delay_seconds=0, sleeper=lambda delay: pytest.fail("Unexpected wait"),
    ) == 42
    assert len(calls) == 3


def test_permanent_timeout_is_sanitized_and_bounded():
    calls = []
    with pytest.raises(DriveReadUnavailable) as exc:
        execute_drive_read(factory([TimeoutError("SECRET_URL") for _ in range(3)], calls),
                           first_delay_seconds=0)
    assert len(calls) == 3
    assert "SECRET_URL" not in str(exc.value)
    assert "Drive" in str(exc.value)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_http_status_retries(status):
    calls = []
    result = execute_drive_read(factory([FakeHttpError(status), [1]], calls),
                                first_delay_seconds=0)
    assert result == [1]
    assert len(calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_non_transient_http_fails_fast(status):
    calls = []
    with pytest.raises(DriveReadRejected) as exc:
        execute_drive_read(factory([FakeHttpError(status)], calls), first_delay_seconds=0)
    assert len(calls) == 1
    assert "private-id-or-url" not in str(exc.value)
    assert str(status) in str(exc.value)


def test_nontransient_programming_error_is_not_retried():
    calls = []
    with pytest.raises(ValueError, match="bad implementation"):
        execute_drive_read(factory([ValueError("bad implementation")], calls), first_delay_seconds=0)
    assert len(calls) == 1


def test_backoff_is_bounded():
    delays = []
    execute_drive_read(factory([TimeoutError("x"), TimeoutError("x"), 9], []),
                       first_delay_seconds=0.4, sleeper=delays.append)
    assert delays == [0.4, 0.8]


def test_response_is_preserved_without_mutating_it():
    response = {"files": [{"name": "synthetic.xlsx"}], "nextPageToken": "fake"}
    assert execute_drive_read(lambda: Request(result=response)) is response


@pytest.mark.parametrize("attempts", [0, 6, -1, True, "3", 2.0])
def test_invalid_attempt_count_blocks(attempts):
    with pytest.raises(ValueError):
        execute_drive_read(lambda: Request(), attempts=attempts)


@pytest.mark.parametrize("seconds", [-1, float("nan"), float("inf"), 6, "0.5", True])
def test_invalid_delay_blocks(seconds):
    with pytest.raises(ValueError):
        execute_drive_read(lambda: Request(), first_delay_seconds=seconds)


def test_invalid_request_factory_blocks():
    with pytest.raises(TypeError):
        execute_drive_read(None)


def test_retry_never_performs_implicit_write():
    calls = []
    class ReadOnly:
        def execute(self):
            calls.append("execute")
            return "ok"
        def upload(self):
            pytest.fail("No upload allowed")
    assert execute_drive_read(ReadOnly) == "ok"
    assert calls == ["execute"]
