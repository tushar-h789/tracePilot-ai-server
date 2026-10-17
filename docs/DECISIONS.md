# TracePilot AI — Architecture Decision Records

Format: lightweight ADRs (Context → Decision → Consequences). Status values: Accepted, Proposed, Deferred.
Date for all Phase 0 ADRs: 2026-10-17.

## ADR-001 — Two repositories (client + server) instead of a monorepo
- **Status**: Accepted (user decision).
- **Context**: A monorepo (single CI, atomic cross-cutting changes) was suggested. The user created two GitHub repos:
  `tushar-h789/tracePilot-ai-client` (Next.js) and `tushar-h789/tracePilot-ai-server` (FastAPI, Celery, Alembic,
  Compose, generator/SDK later). Locally they are sibling folders under `tracePilot-ai/`; note the parent
  directory tree (`/home/tushar`) is an unrelated git repo, so each folder is initialised as its own repo.
- **Decision**: Keep two repos. The **server repo is canonical for docs** and owns `docker-compose.yml`, which can
  build the client from the sibling path `../tracePilot-ai-client` (Compose profile `web`).
- **Consequences / trade-offs**:
  - (−) Cross-repo contract drift and non-atomic changes → mitigated by treating the **server's OpenAPI schema as
    the contract**; the client generates TypeScript types from it (`openapi-typescript`) and CI fails on drift
    (ADR-012).
  - (−) Two CI pipelines and two sets of dependency updates; E2E needs both repos checked out (both repos are
    public, so the server E2E workflow can check out the client without secrets).
  - (−) Compose depends on a sibling-path convention; documented in README.
  - (+) Clear ownership/separation, simpler per-repo tooling (uv vs npm), each repo presentable on its own.
  - Feature work spanning both repos uses matching branch names (e.g. `phase-2/auth`) and linked PRs; server
    merges first, then client.

## ADR-002 — Modular monolith API + separate Celery worker and beat
- **Status**: Accepted.
- **Context**: Solo developer, 6–8 weeks, zero budget; requirements include background AI jobs and schedules.
- **Decision**: One FastAPI codebase with internal modules (auth, orgs, projects, ingestion, logs, metrics,
  incidents, investigations, retention). Same image runs as `api`, `worker`, `beat` processes. No microservices.
- **Consequences**: Simple deploy/test; module boundaries enforced by layering rules, not network calls. Can
  split later if ever needed.

## ADR-003 — Python 3.13 (not the system 3.14)
- **Status**: Accepted.
- **Context**: Host has Python 3.14.4 only. Checked 2026-10-17: FastAPI, Pydantic/pydantic-core (cp314 wheels),
  SQLAlchemy 2.1.5 (cp314 wheels), psycopg 3.3.6, argon2-cffi, redis-py, uvicorn declare 3.14 support. **Celery
  5.6.x README states it runs on Python 3.9–3.13**; its changelog mentions only "initial support for Python 3.14";
  Celery 5.6.3 and kombu 5.6.2 classifiers stop at 3.13.
- **Decision**: Use **Python 3.13** everywhere: `uv python install 3.13` + `requires-python = ">=3.13,<3.14"` on the
  host, `python:3.13-slim` in Docker, 3.13 in CI. Add a non-blocking 3.14 CI job as an early warning.
- **Consequences**: Officially supported combination for the core task queue; revisit when Celery lists 3.14.

## ADR-004 — uv for Python, Bun for the web app (revised in Phase 1)
- **Status**: Accepted (revised 2026-10-17, Phase 1 — user rule change: "use Bun instead of Node/npm wherever
  possible"). Supersedes the Phase 0 decision "npm with `package-lock.json`".
- **Context**: uv 0.11.24, Bun 1.3.14 and Node 24.18 are installed. Next.js 16.4 documents Node.js as its
  supported runtime; Bun can run most Node tooling.
- **Decision**:
  - Python: uv (project, `uv.lock`, Python management) — unchanged.
  - Web app: **Bun is the package manager and script runner** (`bun install --frozen-lockfile`, lockfile
    `bun.lock`, `bun run <script>`, `bun x` for one-off CLIs, `bun audit`), CI uses `oven-sh/setup-bun`, the
    Docker build stages use `oven/bun:1.3.14-slim`.
  - Runtimes, as **verified on the dev machine in Phase 1**:
    | Task | Command | Runtime actually used | Result |
    |---|---|---|---|
    | install | `bun install --frozen-lockfile` | Bun | ✅ |
    | dev / build | `bun run dev` / `bun run build` (`next build`) | Node 24 (the `next` bin has a `node` shebang) | ✅ |
    | build on Bun | `bun --bun next build` | Bun | ✅ works for this app (not the default) |
    | production server | `node .next/standalone/server.js` | Node 24 (Docker `node:24-slim`) | ✅ |
    | production server on Bun | `bun .next/standalone/server.js` | Bun | ✅ smoke-tested (login 200, overview rendered) |
    | unit tests | `bun run test` (Vitest 5) | Node | ✅ |
    | unit tests on Bun | `bun --bun x vitest run` | Bun | ❌ worker termination errors (`stream.readableEnded`) |
    | E2E | `bun run test:e2e` (Playwright 1.64) | Node | ✅ |
    | lint / types | `bun run lint`, `bun run typecheck` | Node | ✅ |
    | Docker build stage | `bun run build` inside `oven/bun` | Bun (`node` is a shim to Bun in that image) | ✅ |
  - Default: keep Node as the runtime for Next.js server, Vitest and Playwright (supported/working); Bun for
    everything package-management related. The Bun runtime may be adopted for the server later after more
    testing.
- **Consequences**: One fast toolchain for installs/scripts; Node remains required on dev machines/CI runners
  (GitHub's ubuntu runners ship Node). `bun audit` replaces `npm audit` (NFR-13).

## ADR-005 — Cookie-based sessions: short-lived access token + rotating refresh session + CSRF double-submit
- **Status**: Accepted.
- **Context**: Browser app; requirement for HttpOnly cookies, revocable sessions, CSRF protection.
- **Decision**: Access token (signed JWT, ~15 min) in `tp_access` HttpOnly cookie; opaque refresh token in
  `tp_refresh` HttpOnly cookie scoped to `/api/v1/auth`, stored hashed in `sessions`, rotated on every use with
  family reuse detection. CSRF: signed double-submit token (`tp_csrf` cookie + `X-CSRF-Token` header) on all unsafe
  methods plus Origin check; SameSite=Lax/Strict; Secure in prod. Browser talks only to the Next.js origin, which
  proxies `/api/v1/*` to the API (same-origin cookies).
- **Consequences**: No tokens in JS-readable storage; a little more server state (sessions table); logout is
  real revocation. Ingestion uses a separate key mechanism (ADR-011).

## ADR-006 — PostgreSQL (incl. full-text search) instead of Elasticsearch/OpenSearch/ClickHouse
- **Status**: Accepted.
- **Context**: Zero budget, laptop resources, MVP volumes (≤ a few million rows).
- **Decision**: Store logs in Postgres 18 with `tsvector` generated column + GIN index for message search, B-tree
  composite indexes for filters, `percentile_cont` for latency. `pg_trgm` optional later.
- **Consequences**: One datastore to operate and test; adequate performance at MVP scale; partitioning and rollups
  documented as future work.

## ADR-007 — Celery + Redis for background jobs and schedules
- **Status**: Accepted (stack mandated).
- **Decision**: Celery 5.6 with Redis 8 as broker (results stored in Postgres rows, not the Celery result backend);
  beat for detection (~60 s) and retention (daily); separate `ai` queue with concurrency 1.
- **Consequences**: Mature retries/timeouts; tasks must be idempotent and take ids only.

## ADR-008 — Local Ollama first, behind a provider abstraction
- **Status**: Accepted.
- **Context**: Zero budget, privacy, CPU-only host (AMD integrated GPU, no NVIDIA; 16 threads, 26 GiB RAM, ~8 GiB
  free at check time).
- **Decision**: `InvestigationProvider` Protocol (ARCHITECTURE §8) with fake + Ollama implementations;
  `AI_PROVIDER=ollama` default; Ollama structured outputs (`format` = JSON schema); default model `qwen2.5:3b`
  (alternatives `llama3.2:3b`, `qwen3:4b`), `num_ctx` capped at 8192, temperature 0.
- **Consequences**: Slow but free and private inference; quality limited by small model → strong validation and
  honest UI labelling. The abstraction is designed and contract-tested **before** integrating any real provider.

## ADR-009 — Server-side evidence validation and AI as display-only data
- **Status**: Accepted.
- **Decision**: AI output is validated (Pydantic) and evidence ids are checked against the stored context snapshot
  and real project rows; AI never changes incident state, is never executed, and is rendered as plain text.
- **Consequences**: Hallucinated references cannot be presented as evidence; prompt injection has no actuator.

## ADR-010 — Gemini free tier only later (P8), opt-in, synthetic data only
- **Status**: Deferred.
- **Context**: Gemini API pricing page (updated 2026-10-16) shows a free tier on selected models, with free-tier
  content "used to improve our products".
- **Decision**: Implement only after the Ollama provider passes the P6 acceptance suite; `AI_PROVIDER=gemini` opt-in;
  never default; documented for synthetic/redacted data only; no paid tier.
- **Consequences**: MVP fully functional without any external AI service.

## ADR-011 — Project-scoped ingestion keys (hash-only storage)
- **Status**: Accepted.
- **Decision**: `tp_<prefix>_<secret>` keys, ≥ 32 random bytes; store HMAC-SHA256 with server pepper; show once;
  revoke/rotate; limits, rate limits and idempotency on ingest; keys authorize ingestion only.
- **Consequences**: Lost keys must be rotated; key compromise impact limited to one project's ingestion.

## ADR-012 — OpenAPI as the cross-repo contract
- **Status**: Accepted.
- **Decision**: FastAPI generates `openapi.json`; server CI exports it; client generates types with
  `openapi-typescript` and CI fails if regenerated types differ from committed ones.
- **Consequences**: Contract drift is caught at PR time; breaking API changes require a coordinated client PR.

## ADR-013 — No Kubernetes; Docker Compose for local and demo
- **Status**: Accepted.
- **Decision**: Docker Compose only; non-root containers, health checks, internal network, localhost-bound ports.
- **Consequences**: Simple, free, reproducible; production deployment is out of scope.

## ADR-014 — Synchronous SQLAlchemy sessions
- **Status**: Accepted (confirmed in P1: `app/db/session.py`, readiness check, Alembic all sync).
- **Decision**: Use sync SQLAlchemy 2.x + psycopg 3 in both API (FastAPI runs sync endpoints in a threadpool) and
  Celery tasks, sharing repositories.
- **Consequences**: Less complexity, one code path; async can be adopted later per endpoint if profiling demands.

## ADR-015 — Local port allocation
- **Status**: Accepted.
- **Context**: Host Postgres on 127.0.0.1:5432; host ports 3000 and 3001 in use.
- **Decision**: Defaults API `127.0.0.1:8000`, web `127.0.0.1:3100`, Postgres `127.0.0.1:5433`, Redis
  `127.0.0.1:6380`, Ollama not published; all overridable via `.env`.

## ADR-016 — Phase 1 dependency and tooling pins (2026-10-17)
- **Status**: Accepted.
- **Decisions / findings**:
  - **redis-py 6.4.0, not 8.x**: `celery[redis]` 5.6.3 → kombu 5.6.2 constrains `redis` below 6.5, so uv resolved
    6.4.0. Revisit when kombu widens the range.
  - **TypeScript 5.9.3, not 7.0**: verified in P1 — with TS 7.0.2 `tsc --noEmit` passes and Next.js 16.4 can
    type-check via the TS CLI, but `eslint-config-next` (typescript-eslint, peer range `<6.1`) fails to load
    because TS 7 ships no JavaScript compiler API. Pinned `typescript@~5.9.3`; revisit when typescript-eslint
    supports TS 7.
  - **Postgres 18 volume path**: the official image declares `VOLUME /var/lib/postgresql` (PGDATA
    `/var/lib/postgresql/18/docker`); compose mounts the named volume there (Docker Hub docs).
  - **No web fonts**: system font stacks instead of `next/font/google`, so builds work offline and no
    third-party font request is made.
  - **Starlette 1.8 TestClient** emits a `StarletteDeprecationWarning` recommending `httpx2`; kept httpx 0.28 for
    now (warning only).
  - GitHub Actions pinned to **commit SHAs** with version comments.

## ADR-017 — Compose: localhost-published Postgres/Redis, explicit migrations, Ollama deferred
- **Status**: Accepted.
- **Decision**: Postgres (`127.0.0.1:5433`) and Redis (`127.0.0.1:6380`, `requirepass`) are published **to the
  loopback interface only** by default because host-run tools (pytest integration tests, `uv run alembic`,
  `scripts/ci-local.sh`) need them; they are password-protected and on non-default ports (ADR-015). Migrations
  are never run implicitly: `docker compose run --rm migrate` (service in the `migrate` profile). The `ollama`
  service is **not** added until Phase 6 (memory; AI not used yet). App containers run read-only, non-root,
  `cap_drop: ALL`, `no-new-privileges`.
- **Consequences**: Anyone with a shell on the dev machine can reach the DB/Redis ports (with the password from
  `.env`); acceptable for local development, documented in README.
