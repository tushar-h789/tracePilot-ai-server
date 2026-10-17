# TracePilot AI — Server

AI-assisted application observability and incident investigation platform (portfolio MVP).
This repository contains the **FastAPI API, Celery worker/beat, Alembic migrations, the Docker Compose stack for
local development, a Bruno API collection and the demo sample service**. The web app lives in
[tracePilot-ai-client](https://github.com/tushar-h789/tracePilot-ai-client).

> Status: **Phase 2 — identity, access control and database foundation**: registration/login with Argon2id,
> server-side cookie sessions + CSRF, rate limiting, organizations, memberships, RBAC (owner/admin/member/viewer),
> audit log. Builds on Phase 1 (health, settings, logging, Alembic, Celery skeleton, Docker, CI). Projects, ingestion
> and incidents arrive in later phases. See [docs/PROGRESS.md](docs/PROGRESS.md).

## Documentation (canonical for the whole system)
[Project plan](docs/PROJECT_PLAN.md) · [Architecture](docs/ARCHITECTURE.md) · [Security](docs/SECURITY.md) ·
[Testing](docs/TESTING.md) · [Decisions (ADRs)](docs/DECISIONS.md) · [Progress](docs/PROGRESS.md)

## Stack
FastAPI · Pydantic v2 · SQLAlchemy 2.1 (sync) · Alembic · PostgreSQL 18 · Redis 8 · Celery 5.6 · Python 3.13 (uv) ·
Docker Compose · Pytest · Ruff · mypy · Bandit · pip-audit · Bruno · GitHub Actions. Ollama (local LLM) arrives in
Phase 6 — **no paid API is needed to run anything**.

## Prerequisites
- Docker Engine + Docker Compose v2 (tested: Docker 29.9, Compose 5.6)
- [uv](https://docs.astral.sh/uv/) ≥ 0.11 (for host tooling/tests). Python 3.13 is installed by uv; the system
  Python is not used.
- Sibling checkout of the client if you want the web container:
  ```
  tracePilot-ai/
    tracePilot-ai-server/   # this repo (docker-compose.yml builds ../tracePilot-ai-client)
    tracePilot-ai-client/
  ```

## Quick start (clean checkout)
```bash
git clone git@github.com:tushar-h789/tracePilot-ai-server.git && cd tracePilot-ai-server
cp .env.example .env
# Edit .env: replace every "change-me-local-only" (use URL-safe characters: letters, digits, - _ .).
# The same password must appear in POSTGRES_PASSWORD / DATABASE_URL / TEST_DATABASE_URL and in
# REDIS_PASSWORD / REDIS_URL / TEST_REDIS_URL. One-liner that does it with a random value:
#   PW=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))'); sed -i "s/change-me-local-only/$PW/g" .env
# Also set SECRET_KEY (signs CSRF tokens; at least 32 characters):
#   SK=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))'); sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$SK/" .env

docker compose up -d --build          # postgres, redis, api, worker, beat (waits for healthy deps)
docker compose run --rm migrate       # apply migrations (explicit, one-shot)
docker compose ps                     # all services "healthy"

curl -i http://127.0.0.1:8000/api/v1/health/live    # 200 {"status":"ok"}
curl -i http://127.0.0.1:8000/api/v1/health/ready   # 200 with database/redis checks (503 if one is down)
open http://127.0.0.1:8000/api/v1/docs              # Swagger UI (disabled when APP_ENV=prod)

# Optional: web app container (builds ../tracePilot-ai-client, ~2 min, ~1 GB RAM while building)
docker compose --profile web up -d --build web      # http://127.0.0.1:3100

docker compose stop                   # stop everything (data stays in the "pgdata" volume)
```

### Ports (all bound to 127.0.0.1, override in `.env`)
| Service | Host port | Variable | Notes |
|---|---|---|---|
| API | 8000 | `API_PORT` | |
| Web (profile `web`) | 3100 | `WEB_PORT` | 3000/3001 are taken on the dev machine |
| Postgres | 5433 | `POSTGRES_HOST_PORT` | host Postgres keeps 5432; for host-run alembic/pytest |
| Redis | 6380 | `REDIS_HOST_PORT` | password-protected; for host-run tests |
| Ollama | — | — | not part of Phase 1 (added in Phase 6, never published) |

## Database migrations (Alembic)
The URL always comes from `DATABASE_URL` (settings); `alembic.ini` contains no credentials.
```bash
docker compose run --rm migrate                         # upgrade head (in a container)
docker compose run --rm migrate alembic current         # show current revision
docker compose run --rm migrate alembic history --verbose
uv run alembic current                                  # same from the host (uses .env → 127.0.0.1:5433)
uv run alembic revision --autogenerate -m "add users"   # new revision (host; formatted with ruff)
uv run alembic downgrade -1                             # roll back one revision
```
Phase 1 ships one empty baseline revision `0001_baseline`.

## Local development without containers for the API
```bash
uv sync                                    # creates .venv with Python 3.13 and locked deps
docker compose up -d postgres redis
uv run uvicorn app.main:create_app --factory --reload --port 8001   # 8000 is used by the api container
uv run celery -A app.workers.celery_app worker --loglevel=INFO --concurrency=1
```

## Quality checks (same as CI)
```bash
docker compose up -d postgres redis
./scripts/ci-local.sh          # ruff, ruff format, mypy, bandit, pytest+coverage (unit+integration),
                               # alembic up/down/check, OpenAPI drift, pip-audit, docker build
SKIP_DOCKER=1 ./scripts/ci-local.sh
```
OpenAPI contract for the client: `uv run python -m scripts.export_openapi` writes `openapi/openapi.json`
(commit it; CI fails if it is stale). The client regenerates its types with `bun run gen:api`.

## Authentication and organizations (Phase 2)
- Browser clients use cookies only: `POST /api/v1/auth/login` sets `tp_session` (HttpOnly) and `tp_csrf`
  (readable). Every unsafe request — including login and register — must send `X-CSRF-Token` equal to the
  `tp_csrf` cookie; get one first with `GET /api/v1/auth/csrf`.
- Registration returns 202 with a generic message and does not sign in. There is no password reset in the MVP.
- Org routes live under `/api/v1/organizations`; non-members get 404, insufficient roles get 403, the last owner
  cannot be removed or demoted (409).
- Design and trade-offs: [ADR-018/019/020](docs/DECISIONS.md); schema and permission matrix:
  [ARCHITECTURE §9a–9d](docs/ARCHITECTURE.md); controls and residual risks: [SECURITY §10](docs/SECURITY.md).

Quick check with curl (dev stack running):
```bash
J=$(mktemp -d)/jar; B=http://127.0.0.1:8000/api/v1
curl -s -c $J -b $J $B/auth/csrf >/dev/null; T=$(awk '$6=="tp_csrf"{print $7}' $J)
curl -s -c $J -b $J -H "X-CSRF-Token: $T" -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"a-long-local-passphrase","display_name":"Me"}' $B/auth/register
curl -s -c $J -b $J -H "X-CSRF-Token: $T" -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"a-long-local-passphrase"}' $B/auth/login
curl -s -b $J $B/auth/me
```

## API collection (Bruno)
`bruno/` is a Bruno collection (OpenCollection YAML). Open it in the Bruno app (Open Collection → `bruno/`) or run
`cd bruno && bunx @usebruno/cli run --env Local` (folders: health, auth, organizations, organization-members,
negative-cases). Conventions: [bruno/README.md](bruno/README.md).

## Demo sample service
`uv run python demo/sample-service/sample_service.py --dry-run --count 5` prints synthetic log events
(sending arrives with ingestion keys in Phase 3). See [demo/sample-service/README.md](demo/sample-service/README.md).

## Configuration
All settings are environment variables validated at startup (`app/core/config.py`); invalid values stop the
process with a message naming the variable (never its value). `APP_ENV=prod` additionally refuses placeholder
credentials, `*` hosts, DEBUG logging and disables `/api/v1/docs` + `/api/v1/openapi.json`.
Key variables: `APP_ENV`, `LOG_LEVEL`, `DATABASE_URL` (`postgresql+psycopg://…`), `REDIS_URL`, `CORS_ORIGINS`,
`ALLOWED_HOSTS`, `AI_PROVIDER` (`ollama`), `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, and since Phase 2 `SECRET_KEY`
(≥ 32 chars), `COOKIE_SECURE` (required `true` in prod; switches to `__Host-` cookie names),
`SESSION_IDLE_TIMEOUT_MINUTES` / `SESSION_ABSOLUTE_TIMEOUT_HOURS`, `ARGON2_*`, `LOGIN_RATE_LIMIT_*`,
`REGISTER_RATE_LIMIT_*`, `TRUSTED_PROXIES` (CIDRs allowed to set `X-Forwarded-For`; empty by default).
See `.env.example`.

## Troubleshooting
- **Port already allocated** (`Bind for 127.0.0.1:8000 failed`): something else listens there. Check with
  `ss -ltnp | grep -E ':(8000|3100|5433|6380)\b'` and change `API_PORT` / `WEB_PORT` / `POSTGRES_HOST_PORT` /
  `REDIS_HOST_PORT` in `.env`. Do not stop the host Postgres on 5432 — the stack does not use it.
- **Postgres 18 volume path**: the postgres:18 image stores data under `/var/lib/postgresql/18/docker` and declares
  the volume at **`/var/lib/postgresql`** (changed in 18). Mounting a volume at `/var/lib/postgresql/data` (the
  pre-18 habit) is wrong. The compose file mounts `pgdata:/var/lib/postgresql`.
- **Changed `POSTGRES_PASSWORD` but login fails**: the password is applied only when the volume is first
  initialised. Either restore the old password or (destroying local data) `docker compose down -v`.
- **`tracepilot_test` database missing** (volume created before the init script existed):
  `docker compose exec postgres createdb -U "$POSTGRES_USER" tracepilot_test` (with values from `.env`).
- **`Invalid configuration: - DATABASE_URL: ...`**: the API refuses to start with bad settings; fix the named
  variable in `.env` (host tools) or the compose environment.
- **Memory**: the backend stack uses ~300 MB RAM (api ≈ 70 MB, worker ≈ 65 MB, beat ≈ 45 MB, postgres ≈ 55 MB,
  redis ≈ 6 MB, web ≈ 60 MB). Building the web image peaks at ~1 GB; build images one at a time on a busy
  machine. The worker runs with `--concurrency=1` and Redis is capped at 128 MB.
- **Readiness 503**: `docker compose ps` / `docker compose logs api` — the response only says which check failed;
  details are in the API's JSON logs (`readiness_check_failed`).
