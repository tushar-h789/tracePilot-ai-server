"""Export the API's OpenAPI document to openapi/openapi.json (the cross-repo contract, ADR-012).

    uv run python -m scripts.export_openapi          # write
    uv run python -m scripts.export_openapi --check  # exit 1 if the committed file is stale

Uses fixed placeholder settings (no connections are opened) so output is deterministic and
independent of any local .env.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.main import create_app

OUTPUT = Path(__file__).resolve().parent.parent / "openapi" / "openapi.json"


def build_schema() -> dict[str, Any]:
    settings = Settings(
        _env_file=None,
        app_env="dev",  # type: ignore[arg-type]
        database_url="postgresql+psycopg://openapi:openapi@localhost:5432/openapi",  # type: ignore[arg-type]
        redis_url="redis://localhost:6379/0",  # type: ignore[arg-type]
        secret_key="openapi-export-placeholder-value-not-a-secret",  # type: ignore[arg-type]  # noqa: S106
        ingest_key_pepper="openapi-export-placeholder-pepper-not-a-secret",  # type: ignore[arg-type]
    )
    return create_app(settings).openapi()


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed file differs")
    args = parser.parse_args(argv)
    content = render(build_schema())
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != content:
            print(f"{OUTPUT} is out of date; run: uv run python -m scripts.export_openapi")
            return 1
        print(f"{OUTPUT} is up to date")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
