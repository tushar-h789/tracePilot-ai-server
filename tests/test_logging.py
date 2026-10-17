import json
import logging

from app.core.logging import REDACTED, JsonFormatter, redact, request_id_var


def _format(**extra: object) -> dict[str, object]:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    for key, value in extra.items():
        setattr(record, key, value)
    result: dict[str, object] = json.loads(JsonFormatter().format(record))
    return result


def test_json_log_line_has_core_fields() -> None:
    token = request_id_var.set("req-12345678")
    try:
        payload = _format(path="/api/v1/health/live")
    finally:
        request_id_var.reset(token)
    assert payload["message"] == "hello world"
    assert payload["level"] == "info"
    assert payload["request_id"] == "req-12345678"
    assert payload["path"] == "/api/v1/health/live"


def test_sensitive_extra_fields_are_redacted() -> None:
    payload = _format(
        password="hunter2",
        authorization="Bearer abc",
        context={"api_key": "k", "nested": {"session_token": "t"}, "ok": 1},
    )
    assert payload["password"] == REDACTED
    assert payload["authorization"] == REDACTED
    assert payload["context"] == {
        "api_key": REDACTED,
        "nested": {"session_token": REDACTED},
        "ok": 1,
    }
    assert "hunter2" not in json.dumps(payload)


def test_redact_handles_lists() -> None:
    assert redact([{"cookie": "c"}, 1]) == [{"cookie": REDACTED}, 1]
