#!/usr/bin/env bash
# Run the same checks as .github/workflows/ci.yml, locally.
#
#   docker compose up -d postgres redis   # integration tests need them
#   ./scripts/ci-local.sh                  # all steps
#   SKIP_DOCKER=1 ./scripts/ci-local.sh    # skip the image build step
#
# TEST_DATABASE_URL / TEST_REDIS_URL are taken from the environment, or from .env.
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

for var in TEST_DATABASE_URL TEST_REDIS_URL; do
  if [[ -z "${!var:-}" && -f .env ]]; then
    value="$(grep -E "^${var}=" .env | tail -n1 | cut -d= -f2- || true)"
    [[ -n "$value" ]] && export "$var=$value"
  fi
done
export TP_REQUIRE_INTEGRATION=1

step "uv sync (locked)"
uv sync --frozen

step "ruff lint";            uv run ruff check .
step "ruff format --check";  uv run ruff format --check .
step "mypy";                 uv run mypy
step "bandit";               uv run bandit -q -c pyproject.toml -r app
step "pytest + coverage";    uv run pytest --cov --cov-report=term-missing
step "alembic upgrade/downgrade (test database)"
DATABASE_URL="$TEST_DATABASE_URL" uv run alembic upgrade head
DATABASE_URL="$TEST_DATABASE_URL" uv run alembic downgrade base
DATABASE_URL="$TEST_DATABASE_URL" uv run alembic upgrade head
DATABASE_URL="$TEST_DATABASE_URL" uv run alembic check
step "OpenAPI drift";        uv run python -m scripts.export_openapi --check
step "pip-audit"
req="$(mktemp)"; trap 'rm -f "$req"' EXIT
uv export --frozen --format requirements-txt --no-emit-project --all-groups -o "$req" >/dev/null
uv run pip-audit --strict --disable-pip -r "$req"
for pkg in packages/python-sdk demo/sample-service; do
  step "$pkg: sync, ruff, format, mypy, pytest"
  (
    cd "$pkg"
    uv sync --frozen
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy
    uv run pytest --cov --cov-report=term-missing -q
  )
done
if [[ "${SKIP_DOCKER:-0}" != "1" ]]; then
  step "docker build"
  docker build -q -t tracepilot-server:ci . >/dev/null && echo "image built: tracepilot-server:ci"
fi
step "all checks passed"
