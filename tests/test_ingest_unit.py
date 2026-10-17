"""Unit tests: ingestion key format/hash, bearer parsing, cursors, contract, value scrubbing."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.errors import ValidationFailedError
from app.core.ingest_keys import (
    bearer_token,
    generate_key,
    hash_key,
    parse_prefix,
    verify_key,
)
from app.core.logging import REDACTED, JsonFormatter, redact, scrub_secrets
from app.core.pagination import decode_cursor, encode_cursor, next_cursor_for
from app.core.permissions import Permission, Role, role_has
from app.schemas.ingest import IngestBatch, LogEventIn

PEPPER = b"unit-test-pepper-unit-test-pepper-0123"


def test_generated_keys_are_high_entropy_and_parseable() -> None:
    keys = {generate_key().plaintext for _ in range(200)}
    assert len(keys) == 200
    sample = generate_key()
    scheme, public_id, secret = sample.plaintext.split("_", 2)
    assert scheme == "tp"
    assert len(public_id) == 12
    assert len(secret) == 43  # token_urlsafe(32) = 256 bits
    assert parse_prefix(sample.plaintext) == sample.prefix == f"tp_{public_id}"
    assert sample.plaintext not in repr(sample)


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "tp_",
        "tp_ABCDEF012345_" + "a" * 43,
        "tp_0123456789ab_" + "a" * 42,
        "x" * 200,
        "tp_0123456789ab_" + "a" * 43 + "extra",
        "sk_0123456789ab_" + "a" * 43,
    ],
)
def test_malformed_keys_have_no_prefix(candidate: str) -> None:
    assert parse_prefix(candidate) is None


def test_hash_is_peppered_hmac_and_verification_is_exact() -> None:
    key = generate_key().plaintext
    digest = hash_key(PEPPER, key)
    assert len(digest) == 64
    assert digest != hash_key(b"another-pepper-another-pepper-0000", key)
    assert verify_key(PEPPER, key, digest)
    assert not verify_key(PEPPER, key[:-1] + ("a" if key[-1] != "a" else "b"), digest)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("", None),
        ("Bearer abc", "abc"),
        ("bearer   abc  ", "abc"),
        ("Basic abc", None),
        ("Bearer", None),
        ("Bearer ", None),
    ],
)
def test_bearer_token(header: str | None, expected: str | None) -> None:
    assert bearer_token(header) == expected


def test_cursor_round_trip_and_next_cursor() -> None:
    ids = [uuid.uuid4() for _ in range(3)]
    assert decode_cursor(encode_cursor(ids[0])) == ids[0]
    assert decode_cursor(None) is None
    assert next_cursor_for(ids, 3) is None
    assert decode_cursor(next_cursor_for(ids, 2)) == ids[1]


@pytest.mark.parametrize("bad", ["%%%", "eyJ2IjoyLCJpZCI6IngifQ", "bm90LWpzb24", "W10"])
def test_invalid_cursors_are_422(bad: str) -> None:
    with pytest.raises(ValidationFailedError):
        decode_cursor(bad)


def _event(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
        "level": "info",
        "service_name": "api",
        "message": "hello",
    }
    base.update(overrides)
    return base


def test_contract_accepts_minimal_event_and_rejects_extras() -> None:
    event = LogEventIn.model_validate(_event())
    assert event.attributes == {}
    assert event.environment is None
    with pytest.raises(ValidationError):
        LogEventIn.model_validate(_event(project_id=str(uuid.uuid4())))
    with pytest.raises(ValidationError):
        IngestBatch.model_validate({"events": [_event()], "org_id": "x"})


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_finite_numbers_rejected(value: float) -> None:
    with pytest.raises(ValidationError):
        LogEventIn.model_validate(_event(duration_ms=value))
    with pytest.raises(ValidationError):
        LogEventIn.model_validate(_event(attributes={"x": value}))


def test_level_enum_matches_contract() -> None:
    for level in ("debug", "info", "warning", "error", "critical"):
        assert LogEventIn.model_validate(_event(level=level)).level == level


def test_scrub_secrets_keeps_only_public_prefix() -> None:
    key = generate_key()
    text = f"Authorization header was Bearer {key.plaintext} (oops)"
    scrubbed = scrub_secrets(text)
    assert key.plaintext not in scrubbed
    assert f"{key.prefix}_[REDACTED]" in scrubbed
    assert scrub_secrets("nothing to see") == "nothing to see"


def test_redaction_covers_ingestion_key_fields_and_values() -> None:
    key = generate_key().plaintext
    out = redact(
        {
            "ingestion_key": key,
            "ingest_key": key,
            "Authorization": f"Bearer {key}",
            "plaintext": key,
            "note": f"leaked {key}",
            "prefix": "tp_0123456789ab",
        }
    )
    assert out["ingestion_key"] == out["ingest_key"] == out["Authorization"] == REDACTED
    assert out["plaintext"] == REDACTED
    assert key not in out["note"]
    assert out["prefix"] == "tp_0123456789ab"


def test_formatter_scrubs_keys_from_messages() -> None:
    key = generate_key().plaintext
    record = logging.makeLogRecord({"msg": "got %s", "args": (key,), "levelno": 20})
    rendered = JsonFormatter().format(record)
    assert key not in rendered


def test_phase3_permission_matrix() -> None:
    assert role_has(Role.ADMIN, Permission.PROJECT_MANAGE)
    assert role_has(Role.ADMIN, Permission.KEYS_MANAGE)
    assert not role_has(Role.MEMBER, Permission.PROJECT_MANAGE)
    assert not role_has(Role.MEMBER, Permission.KEYS_MANAGE)
    assert role_has(Role.MEMBER, Permission.KEYS_READ_METADATA)
    assert not role_has(Role.VIEWER, Permission.KEYS_READ_METADATA)
    assert role_has(Role.VIEWER, Permission.PROJECT_READ)
