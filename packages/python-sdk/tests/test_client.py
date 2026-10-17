"""SDK tests with httpx.MockTransport (no network)."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from pydantic import ValidationError as PydanticValidationError

from tracepilot_sdk import (
    AuthenticationError,
    ConfigurationError,
    LogEvent,
    RateLimitError,
    ServerError,
    TracePilotClient,
    TracePilotError,
    TransportError,
    ValidationError,
)

# Built from pieces so secret scanners do not flag this obviously fake fixture.
KEY = "tp_" + "0123456789ab" + "_" + "S" * 43
Handler = Callable[[httpx.Request], httpx.Response]


def ok(request: httpx.Request) -> httpx.Response:
    n = len(json.loads(request.content)["events"])
    return httpx.Response(202, json={"received": n, "accepted": n, "duplicates": 0, "rejected": 0})


class Recorder:
    def __init__(self, *responses: httpx.Response | Exception | Handler) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx.Response):
            return item
        return item(request)


def make_client(
    recorder: Recorder, sleeps: list[float] | None = None, **kwargs: Any
) -> TracePilotClient:
    record = sleeps if sleeps is not None else []
    return TracePilotClient(
        "http://tracepilot.test",
        KEY,
        transport=httpx.MockTransport(recorder),
        sleep=record.append,
        jitter=lambda: 1.0,
        **kwargs,
    )


def event(i: int = 0, **extra: Any) -> LogEvent:
    return LogEvent(service_name="checkout", message=f"hello {i}", **extra)


def test_success_sends_bearer_key_and_contract_payload() -> None:
    recorder = Recorder(ok)
    with make_client(recorder) as client:
        result = client.send([event(1, level="error", http_status=500, duration_ms=3.5)])
    assert (result.received, result.accepted, result.duplicates, result.batches) == (1, 1, 0, 1)
    request = recorder.requests[0]
    assert request.url.path == "/api/v1/ingest/logs"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["content-type"] == "application/json"
    assert "cookie" not in request.headers
    sent = json.loads(request.content)["events"][0]
    assert sent["level"] == "error"
    assert sent["http_status"] == 500
    assert "trace_id" not in sent  # None fields omitted
    uuid.UUID(sent["event_id"])
    assert datetime.fromisoformat(sent["timestamp"]).tzinfo is not None


def test_retry_then_success_on_503_and_connect_error() -> None:
    sleeps: list[float] = []
    recorder = Recorder(
        httpx.Response(503, json={"detail": "busy"}),
        httpx.ConnectError("refused"),
        ok,
    )
    with make_client(recorder, sleeps) as client:
        result = client.send([event()])
    assert result.accepted == 1
    assert len(recorder.requests) == 3
    assert sleeps == [0.5, 1.0]  # exponential backoff (jitter fixed to 1.0 in tests)
    # Every attempt re-sends the same event_id, so the server de-duplicates retries.
    ids = {json.loads(r.content)["events"][0]["event_id"] for r in recorder.requests}
    assert len(ids) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 415, 422])
def test_no_retry_on_4xx(status: int) -> None:
    sleeps: list[float] = []
    recorder = Recorder(httpx.Response(status, json={"detail": "nope", "code": "x"}))
    with make_client(recorder, sleeps) as client, pytest.raises(TracePilotError) as info:
        client.send([event()])
    assert len(recorder.requests) == 1
    assert sleeps == []
    expected = AuthenticationError if status == 401 else ValidationError
    assert isinstance(info.value, expected)
    assert info.value.status_code == status


def test_validation_error_exposes_field_errors() -> None:
    errors = [{"loc": ["body", "events", 0, "level"], "msg": "bad", "type": "literal_error"}]
    recorder = Recorder(httpx.Response(422, json={"detail": "invalid", "errors": errors}))
    with make_client(recorder) as client, pytest.raises(ValidationError) as info:
        client.send([event()])
    assert info.value.errors == errors


def test_429_honours_retry_after() -> None:
    sleeps: list[float] = []
    recorder = Recorder(httpx.Response(429, headers={"Retry-After": "7"}, json={}), ok)
    with make_client(recorder, sleeps) as client:
        assert client.send([event()]).accepted == 1
    assert sleeps == [7.0]


def test_429_gives_up_after_max_retries() -> None:
    sleeps: list[float] = []
    recorder = Recorder(httpx.Response(429, headers={"Retry-After": "1"}, json={"detail": "slow"}))
    with (
        make_client(recorder, sleeps, max_retries=2) as client,
        pytest.raises(RateLimitError) as info,
    ):
        client.send([event()])
    assert len(recorder.requests) == 3
    assert info.value.retry_after == 1.0
    assert sleeps == [1.0, 1.0]


def test_429_with_excessive_retry_after_is_raised_immediately() -> None:
    recorder = Recorder(httpx.Response(429, headers={"Retry-After": "3600"}, json={}))
    with make_client(recorder) as client, pytest.raises(RateLimitError):
        client.send([event()])
    assert len(recorder.requests) == 1


def test_persistent_5xx_raises_server_error() -> None:
    recorder = Recorder(httpx.Response(500, text="boom"))
    with make_client(recorder, max_retries=1) as client, pytest.raises(ServerError):
        client.send([event()])
    assert len(recorder.requests) == 2


def test_timeout_is_retried_then_raised_as_transport_error() -> None:
    sleeps: list[float] = []
    recorder = Recorder(httpx.ReadTimeout("slow"))
    with make_client(recorder, sleeps, max_retries=2) as client, pytest.raises(TransportError):
        client.send([event()])
    assert len(recorder.requests) == 3
    assert sleeps == [0.5, 1.0]


def test_backoff_is_capped() -> None:
    sleeps: list[float] = []
    recorder = Recorder(httpx.Response(503, json={}))
    with (
        make_client(recorder, sleeps, max_retries=6, backoff_max=2.0) as client,
        pytest.raises(ServerError),
    ):
        client.send([event()])
    assert max(sleeps) == 2.0


def test_auto_chunking_by_count() -> None:
    recorder = Recorder(ok)
    with make_client(recorder, max_batch_size=500) as client:
        result = client.send(event(i) for i in range(1201))
    sizes = [len(json.loads(r.content)["events"]) for r in recorder.requests]
    assert sizes == [500, 500, 201]
    assert (result.received, result.accepted, result.batches) == (1201, 1201, 3)


def test_auto_chunking_by_bytes() -> None:
    recorder = Recorder(ok)
    with make_client(recorder, max_batch_bytes=20_000) as client:
        client.send(event(i, attributes={"blob": "x" * 4000}) for i in range(10))
    assert len(recorder.requests) > 1
    assert all(len(r.content) <= 20_000 for r in recorder.requests)


def test_dict_events_are_validated_locally() -> None:
    recorder = Recorder(ok)
    with make_client(recorder) as client:
        client.send([{"service_name": "api", "message": "from dict"}])
        with pytest.raises(PydanticValidationError):
            client.send([{"service_name": "api", "message": "x", "project_id": "other"}])
    assert len(recorder.requests) == 1


def test_event_model_rejects_naive_timestamps_and_bad_levels() -> None:
    with pytest.raises(PydanticValidationError):
        LogEvent(service_name="a", message="m", timestamp=datetime(2026, 1, 1))
    with pytest.raises(PydanticValidationError):
        LogEvent(service_name="a", message="m", level="warn")
    aware = LogEvent(
        service_name="a", message="m", timestamp=datetime.now(timezone(timedelta(hours=6)))
    )
    assert aware.timestamp.utcoffset() == timedelta(hours=6)
    assert LogEvent(service_name="a", message="m").timestamp.tzinfo == UTC


def test_key_from_environment_and_redacted_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACEPILOT_INGEST_KEY", KEY)
    monkeypatch.setenv("TRACEPILOT_ENDPOINT", "http://env.test/")
    client = TracePilotClient()
    try:
        assert client.endpoint == "http://env.test"
        text = repr(client)
        assert "S" * 43 not in text
        assert "tp_0123456789ab_***" in text
        assert KEY not in str(vars(client).get("endpoint"))
    finally:
        client.close()


def test_missing_key_or_bad_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRACEPILOT_INGEST_KEY", raising=False)
    with pytest.raises(ConfigurationError):
        TracePilotClient("http://x.test")
    with pytest.raises(ConfigurationError):
        TracePilotClient("ftp://x.test", KEY)
    with pytest.raises(ConfigurationError):
        TracePilotClient("http://x.test", KEY, max_batch_size=501)
    with pytest.raises(ConfigurationError):
        TracePilotClient("http://x.test", KEY, max_retries=11)


def test_key_never_appears_in_logs_or_errors(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    recorder = Recorder(
        httpx.Response(503, json={"detail": "busy"}),
        httpx.Response(401, json={"detail": "A valid ingestion key is required."}),
    )
    with make_client(recorder) as client, pytest.raises(AuthenticationError) as info:
        client.send([event()])
    assert "tracepilot_retry" in caplog.text
    assert KEY not in caplog.text
    assert KEY not in str(info.value)
    assert KEY not in repr(info.value)
