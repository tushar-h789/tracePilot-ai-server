import json
import subprocess  # nosec B404 - runs our own script with a fixed argv
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "demo" / "sample-service" / "sample_service.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
    )


def test_dry_run_prints_valid_events() -> None:
    result = _run("--dry-run", "--count", "3", "--seed", "7")
    assert result.returncode == 0
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(events) == 3
    for event in events:
        assert event["level"] in {"info", "warn", "error"}
        assert 100 <= event["http_status"] <= 599
        assert event["attributes"]["synthetic"] is True


def test_sending_is_not_implemented_yet() -> None:
    assert _run("--count", "1").returncode == 2
