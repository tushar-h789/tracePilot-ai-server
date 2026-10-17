"""Determinism and scenario-shape tests (no network)."""

from __future__ import annotations

import io
import json
import statistics
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from tracepilot_sdk import LogEvent, TracePilotClient

from sample_service.cli import main
from sample_service.scenarios import SCENARIOS, event_id, generate_events

START = datetime(2026, 10, 17, 6, 0, tzinfo=UTC)


def run(scenario: str, seed: int = 42, count: int = 2000, duration: int = 600) -> list[LogEvent]:
    return list(
        generate_events(scenario, seed=seed, start=START, duration_seconds=duration, count=count)
    )


def error_rate(events: list[LogEvent]) -> float:
    return sum(1 for e in events if e.level == "error") / len(events)


def median_ms(events: list[LogEvent], service: str | None = None) -> float:
    return statistics.median(
        e.duration_ms or 0 for e in events if service is None or e.service_name == service
    )


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_same_seed_gives_identical_output(scenario: str) -> None:
    first = [e.to_payload() for e in run(scenario, count=300)]
    second = [e.to_payload() for e in run(scenario, count=300)]
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_different_seed_gives_different_output(scenario: str) -> None:
    a = [e.to_payload() for e in run(scenario, seed=1, count=100)]
    b = [e.to_payload() for e in run(scenario, seed=2, count=100)]
    assert a != b
    assert {x["event_id"] for x in a}.isdisjoint({x["event_id"] for x in b})


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_events_are_demo_tagged_valid_and_inside_window(scenario: str) -> None:
    events = run(scenario, count=500, duration=300)
    ids = [e.event_id for e in events]
    assert len(set(ids)) == len(ids)
    assert (
        ids[7]
        == event_id(scenario, 42, 7)
        == uuid.uuid5(uuid.UUID("6f1c3c2e-6d1b-5c55-9c3e-7a1f0d1e2b40"), f"{scenario}:42:7")
    )
    for e in events:
        assert e.environment == "demo"
        assert e.attributes["synthetic"] is True
        assert e.attributes["scenario"] == scenario
        assert START <= e.timestamp < START + timedelta(seconds=300)
        assert e.http_status is not None
        assert 100 <= e.http_status <= 599
        assert (e.level == "error") == (e.http_status >= 500)
    timestamps = [e.timestamp for e in events]
    assert timestamps == sorted(timestamps)


def test_event_ids_do_not_depend_on_start_time() -> None:
    a = list(generate_events("normal", seed=5, start=START, duration_seconds=60, count=10))
    b = list(
        generate_events(
            "normal", seed=5, start=START + timedelta(hours=1), duration_seconds=60, count=10
        )
    )
    assert [e.event_id for e in a] == [e.event_id for e in b]
    assert a[0].timestamp != b[0].timestamp


def test_scenario_shapes() -> None:
    normal = run("normal")
    assert error_rate(normal) < 0.03
    assert 60 < median_ms(normal) < 110

    latency = run("latency")
    assert median_ms(latency, "checkout-api") > 600
    assert median_ms(latency, "web-frontend") < 120
    slow = [e.duration_ms or 0 for e in latency if e.service_name == "checkout-api"]
    assert statistics.quantiles(slow, n=20)[18] > 1800  # p95 long tail

    http500 = run("http-500")
    assert 0.18 < error_rate(http500) < 0.35
    assert all(e.service_name == "checkout-api" for e in http500 if e.level == "error")
    assert {e.http_status for e in http500 if e.level == "error"} == {500}

    db = run("db-timeout")
    db_errors = [e for e in db if e.level == "error"]
    assert 0.1 < len(db_errors) / len(db) < 0.3
    assert all(e.attributes["error.type"] == "DatabaseTimeout" for e in db_errors)
    assert all((e.duration_ms or 0) >= 5000 for e in db_errors)

    upstream = run("external-api-failure")
    up_errors = [e for e in upstream if e.level == "error"]
    assert all(e.http_status == 502 for e in up_errors)
    assert all(e.attributes["error.type"] == "UpstreamError" for e in up_errors)

    recovery = run("recovery")
    first, last = recovery[: len(recovery) // 4], recovery[-len(recovery) // 4 :]
    assert error_rate(first) > 3 * max(error_rate(last), 0.005)
    assert median_ms(first, "checkout-api") > 2 * median_ms(last, "checkout-api")


def test_generator_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="unknown scenario"):
        list(generate_events("nope", seed=1, start=START, duration_seconds=1, count=1))
    with pytest.raises(ValueError, match="timezone"):
        list(
            generate_events(
                "normal", seed=1, start=datetime(2026, 1, 1), duration_seconds=1, count=1
            )
        )
    with pytest.raises(ValueError, match="positive"):
        list(generate_events("normal", seed=1, start=START, duration_seconds=1, count=0))


def test_cli_dry_run_is_reproducible() -> None:
    argv = [
        "--scenario",
        "db-timeout",
        "--seed",
        "9",
        "--start",
        "2026-10-17T06:00:00Z",
        "--duration",
        "120",
        "--count",
        "25",
        "--dry-run",
    ]
    out1, out2 = io.StringIO(), io.StringIO()
    assert main(argv, stdout=out1) == 0
    assert main(argv, stdout=out2) == 0
    assert out1.getvalue() == out2.getvalue()
    lines = [json.loads(line) for line in out1.getvalue().splitlines()]
    assert len(lines) == 25
    assert all(line["environment"] == "demo" for line in lines)


def test_cli_requires_key_from_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TRACEPILOT_INGEST_KEY", raising=False)
    assert main(["--count", "1"]) == 2
    assert "TRACEPILOT_INGEST_KEY" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--count", "0"],
        ["--duration", "0"],
        ["--batch-size", "501"],
        ["--start", "2026-10-17T06:00:00"],
        ["--scenario", "nope"],
    ],
)
def test_cli_rejects_bad_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main([*argv, "--dry-run"])
    assert info.value.code == 2


def test_cli_sends_through_sdk_and_reports_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    key = "tp_0123456789ab_" + "K" * 43
    monkeypatch.setenv("TRACEPILOT_INGEST_KEY", key)
    seen: dict[str, int] = {}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        events = json.loads(request.content)["events"]
        accepted = 0
        for e in events:
            if e["event_id"] not in seen:
                seen[e["event_id"]] = 1
                accepted += 1
        return httpx.Response(
            202,
            json={
                "received": len(events),
                "accepted": accepted,
                "duplicates": len(events) - accepted,
                "rejected": 0,
            },
        )

    def factory(endpoint: str, k: str) -> TracePilotClient:
        return TracePilotClient(
            endpoint, k, transport=httpx.MockTransport(handler), max_batch_size=100
        )

    argv = ["--scenario", "http-500", "--count", "250", "--start", "2026-10-17T06:00:00Z"]
    first, second = io.StringIO(), io.StringIO()
    assert main(argv, stdout=first, client_factory=factory) == 0
    assert main(argv, stdout=second, client_factory=factory) == 0
    one, two = json.loads(first.getvalue()), json.loads(second.getvalue())
    assert (one["accepted"], one["duplicates"], one["batches"]) == (250, 0, 3)
    assert (two["accepted"], two["duplicates"]) == (0, 250)  # idempotent rerun
    assert all(r.headers["authorization"] == f"Bearer {key}" for r in requests)
    assert key not in first.getvalue() + second.getvalue()


def test_cli_reports_api_errors_without_the_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    key = "tp_0123456789ab_" + "Q" * 43
    monkeypatch.setenv("TRACEPILOT_INGEST_KEY", key)

    def factory(endpoint: str, k: str) -> TracePilotClient:
        return TracePilotClient(
            endpoint,
            k,
            transport=httpx.MockTransport(
                lambda r: httpx.Response(401, json={"detail": "A valid ingestion key is required."})
            ),
        )

    assert main(["--count", "3"], client_factory=factory) == 1
    err = capsys.readouterr().err
    assert "AuthenticationError" in err
    assert key not in err
