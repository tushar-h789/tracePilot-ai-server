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
- **Status**: Superseded by ADR-018 (Phase 2, 2026-10-17). The CSRF double-submit, Origin check and same-origin
  proxy parts carry over unchanged; the JWT access token + rotating refresh token were replaced by one opaque,
  server-side session.
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

## ADR-018 — Opaque server-side sessions in an HttpOnly cookie (supersedes ADR-005's JWT + refresh design)
- **Status**: Accepted (Phase 2, 2026-10-17).
- **Context**: Phase 0 planned a 15-minute JWT access cookie plus a rotating refresh cookie with reuse detection.
  For a single API instance with Postgres already on every request path, that design costs two token types,
  rotation races and a revocation gap of up to 15 minutes, while buying a DB lookup we do not need to avoid.
- **Decision**:
  - Login issues a 256-bit random token (`secrets.token_urlsafe(32)`) in cookie `tp_session`
    (`__Host-tp_session` when `COOKIE_SECURE=true`): HttpOnly, SameSite=Lax, Path=/, Secure in prod,
    Max-Age = absolute lifetime.
  - Only `sha256(token)` is stored (`sessions.token_hash`, unique). A DB leak does not yield usable cookies;
    SHA-256 (not Argon2) is fine because the token has 256 bits of entropy.
  - Every authenticated request loads the session and the user: rejected if revoked, past `expires_at`
    (absolute, 24 h default), idle for longer than 60 min (default), or the user is inactive. `last_seen_at` is
    written at most once per 60 s to keep writes low.
  - Login revokes any session the browser presented (session fixation defence) and always issues a new token.
    Logout revokes the current session; logout-all revokes every session of the user; a single session can be
    revoked from the sessions list.
  - Passwords: Argon2id via `argon2-cffi` with explicit parameters (t=3, m=64 MiB, p=4, 32-byte hash, 16-byte
    salt — above the OWASP minimum of m=19 MiB/t=2); hashes are upgraded on successful login when the parameters
    change (`needs_rehash`). Production refuses `ARGON2_MEMORY_COST_KIB < 19456`.
  - Registration never logs in automatically and always answers 202 with the same body (see ADR-019).
  - Password reset, email verification and MFA are out of scope for the MVP (no outbound email exists).
- **Trade-offs**: + immediate, real revocation; + one token type, no rotation races, nothing to decode
  client-side; + simple to test. − one indexed lookup per request (≈ sub-millisecond on the dev box) and a write
  per minute per active session; − horizontal scaling needs the shared DB (already true). Revisit if the API is
  ever served from multiple regions.

## ADR-019 — Tenant-isolation responses, account enumeration stance and rate limiting
- **Status**: Accepted (Phase 2, 2026-10-17).
- **404 vs 403**: a caller who is not a member of an organization gets **404 "Organization not found."** for every
  org-scoped route — identical to a random UUID — so org ids cannot be probed. A member whose role lacks the
  permission gets **403** (they already know the org exists; a clear 403 is better UX and easier to audit).
  Membership is always resolved from the DB from the session's user id; no endpoint takes the scope from the body.
  Projects (Phase 3) reuse the same dependency (`authorize_org`) and the same rule.
- **Enumeration stance**:
  - Login failures are one generic 401 "Invalid email or password." for unknown email, wrong password and
    inactive account; unknown emails verify against a dummy Argon2 hash so timing is comparable.
  - Registration always returns 202 with one fixed message ("Registration received. If this email address was
    not already registered, your account is ready and you can sign in.") — duplicates still
    hash the password (same timing) and are recorded as `auth.register_duplicate` in the audit log. The user is
    never signed in by registration, so a 202 reveals nothing.
  - Accepted leak: an org owner/admin adding a member by email learns whether that email has an account
    (404 "No registered user with that email address." vs 201). This is limited to authenticated owners/admins acting inside
    their own org and successful adds are audited; it is not separately rate limited (follow-up). Invitations by email would remove it but
    need outbound email (out of scope).
- **Rate limiting** (Redis, fixed window: `INCR` + `EXPIRE … NX`, remaining TTL returned as `Retry-After`):
  - login per email (`rl:login:email:<sha256(email)>`) 5 / 60 s; login per IP 20 / 60 s;
    register per IP 20 / 600 s. All configurable via `LOGIN_*` / `REGISTER_*` settings.
  - Keys hash the email/IP so Redis never holds raw PII. Limits apply before password verification, so a
    rate-limited attempt costs no Argon2 work. Responses: 429 problem+json + `Retry-After`; audited as
    `auth.login_rate_limited`.
  - Fails **closed**: if Redis is unavailable, login/register return 503 rather than allowing unlimited guessing.
  - The client IP is the socket peer unless the peer is in `TRUSTED_PROXIES` (empty by default), in which case the
    right-most untrusted `X-Forwarded-For` hop is used. The Next.js proxy does not forward XFF today, so all
    browser traffic shares the proxy's IP bucket locally — acceptable for a local demo; documented in SECURITY.md.
  - Trade-off: a fixed window allows up to 2× the limit across a window boundary; a sliding window or token
    bucket is unnecessary at these limits.
- **Last owner / escalation**: owners can do everything; admins manage admin/member/viewer but cannot grant,
  modify or remove an owner; nobody can raise their own role; removing or demoting the last owner → 409. Member
  changes lock the organization row (`SELECT … FOR UPDATE`) so two concurrent demotions cannot leave zero owners.

## ADR-020 — Phase 2 naming, roles and build adjustments
- **Status**: Accepted (Phase 2, 2026-10-17).
- **Roles**: all four Phase 0 roles are kept — owner, admin, member, **viewer** (read-only). The viewer role costs
  one matrix row and is what PROJECT_PLAN §5 specifies for stakeholders; dropping it later would need a data
  migration. The matrix lives in one place: `app/core/permissions.py` (see ARCHITECTURE.md §9a).
- **Naming**: Phase 0 names were changed for consistency with the plural-table convention:
  `memberships` → `organization_members`, `audit_log` → `audit_logs` (now required, not optional), and the API
  prefix `/orgs` → `/organizations`. `sessions` holds server-side sessions (ADR-018) instead of refresh sessions.
  Audit metadata column is `metadata` (jsonb); the ORM attribute is `details` because `metadata` is reserved by
  SQLAlchemy.
- **CSRF on login/register**: the double-submit check also applies to login and register (login CSRF defence);
  the client fetches `GET /auth/csrf` first. The token is `nonce.HMAC(SECRET_KEY, "csrf|binding|nonce")` where
  `binding` is `sha256(session cookie)` or `anon`, so a token minted for one session is useless for another.
- **Same-origin proxy**: the Next.js route handler `src/app/api/v1/[...path]/route.ts` forwards browser calls to
  `API_BASE_URL` at request time (header allowlists, 1 MiB body cap, 15 s timeout, `Set-Cookie` passthrough), so
  cookies are first-party and the API needs no credentialed CORS for the browser.
- **Low-RAM builds**: `next.config.ts` honours `NEXT_BUILD_CPUS` (worker count) and `NEXT_SKIP_TYPECHECK=1`
  (typecheck runs separately via `bun run typecheck`). The default 15 workers peaked above 1.3 GB; one worker
  peaks at ≈ 0.56 GB. The client Dockerfile sets both.

## ADR-021 — Ingestion keys, ingest authentication, idempotency and validation (refines ADR-011)
- **Status**: Accepted (Phase 3, 2026-10-17).
- **Key format**: `tp_<12 hex public id>_<secrets.token_urlsafe(32)>` (43 chars, 256 bits of entropy).
  - Regex: `^tp_([0-9a-f]{12})_([A-Za-z0-9_-]{43})$`.
  - `tp_<id>` is the stored, unique, non-secret **prefix**. It is used for lookup, in the UI and in logs.
  - The recognizable shape lets secret scanners and our own log scrubber (`INGEST_KEY_VALUE_RE`) find leaked keys.
- **Hashing**: `key_hash = HMAC-SHA256(INGEST_KEY_PEPPER, full key)` (64 hex). Verification is lookup by prefix,
  then `hmac.compare_digest`.
  - A fast keyed hash is enough because the secret is 256-bit random; a slow password hash adds nothing here and
    would make ingest a CPU DoS target.
  - The pepper (≥ 32 chars) lives only in the environment; a database dump alone cannot verify keys.
  - Production refuses a placeholder pepper.
  - *Trade-off*: rotating the pepper invalidates every key. There is no pepper versioning in the MVP.
- **Plaintext once**: only `POST …/ingestion-keys` and `POST …/{id}/rotate` return `secret`, with
  `Cache-Control: no-store` and `Pragma: no-cache`. Lists return metadata: prefix, name, status, created_at,
  last_used_at, expires_at, revoked_at, replaced_by_id.
- **Rotation**: rotation creates a new key with the same name. The old key gets `replaced_by_id`, and then:
  - `grace_seconds=0` (the default): the old key is revoked immediately;
  - otherwise the old key gets `expires_at = now + grace` (≤ `KEY_ROTATION_MAX_GRACE_SECONDS`, 86 400);
  - only active keys can be rotated (409).
- **Limits**: at most `MAX_ACTIVE_KEYS_PER_PROJECT` (10) active keys; the project row is locked during creation.
  - Archived projects cannot get keys.
  - Owner/admin manage keys. Members can read key metadata (they operate services and need to see `last_used_at`).
    Viewers cannot.
  - Every create, revoke and rotate writes an audit event with the prefix, never the secret.
- **Ingest auth**: only `Authorization: Bearer <key>`.
  - Cookies are never read, so there is no CSRF exposure and no CSRF check.
  - CORS does not allow the `Authorization` header, so browsers cannot call ingest cross-origin. The web proxy
    does not forward it either.
  - Every failure returns the same 401: `invalid_ingestion_key`, "A valid ingestion key is required.",
    `WWW-Authenticate: Bearer`. Failures covered: missing, malformed, unknown, wrong secret, revoked, expired,
    archived project.
  - The reason is logged server-side with the prefix only.
  - `last_used_at` is updated at most once per `INGEST_KEY_TOUCH_INTERVAL_SECONDS` (60 s), with a conditional
    UPDATE, to avoid a write per request.
- **Project binding**: the project comes from the key. `project_id` anywhere in the payload is a 422
  (`extra="forbid"`), so a key can never write into another project.
- **Idempotency (replaces the `Idempotency-Key` / `ingest_batches` sketch in ARCHITECTURE §6)**:
  - The client supplies an `event_id` (UUID) per event; the SDK and demo do so deterministically.
  - `log_events` has `UNIQUE (project_id, event_id)`; inserts use `ON CONFLICT DO NOTHING RETURNING id`.
  - The response reports `received`, `accepted`, `duplicates`, `rejected` (always 0, see below).
  - **Scope**: per project, for as long as the original row exists, so retention bounds the window. The same
    `event_id` in two projects gives two rows.
  - Duplicates within one batch are also collapsed.
  - *Why*: per-event ids make partial retries, SDK chunking and demo reruns safe without storing request hashes.
    Batch replay detection would not handle re-chunked retries.
- **Validation (all-or-nothing)**:
  - Content-Type must be `application/json`; gzip is refused (415).
  - `Content-Length` over `INGEST_MAX_BODY_BYTES` (1 MiB) → 413 before reading. A streamed body is counted and
    cut off at the same limit.
  - The per-key request rate limit runs before parsing.
  - Strict pydantic contract (`extra="forbid"`, bounded strings, enums, finite numbers, attribute depth/keys/size,
    no NUL).
  - Batch over `INGEST_MAX_BATCH_EVENTS` (500) → 422.
  - Timestamps outside [now − 7 d, now + 5 min] → 422.
  - The per-project events limit then uses `INCRBY` by batch size.
  - Any invalid event rejects the whole batch with a 422 listing up to 50 errors by `loc` and `type`, without
    echoing input.
  - *Why*: partial acceptance makes retries ambiguous. Clients fix and resend; idempotency makes resending safe.
- **Redaction at ingest**: attributes go through the sensitive-key denylist (`password`, `token`, `ingest_key`, …)
  and every string goes through `scrub_secrets` before storage. `attributes.synthetic is true` sets
  `log_events.is_synthetic`.
- **Fail closed**: if Redis is unavailable, ingest answers 503 instead of skipping rate limits.

## ADR-022 — Cursor pagination envelope
- **Status**: Accepted (Phase 3, 2026-10-17).
- **Decision**: new list endpoints (`/organizations/{id}/projects`, `/projects/{id}/ingestion-keys`, later logs)
  return `{items, next_cursor}`.
  - Keyset pagination, newest first, by UUIDv7 `id` (time-ordered, unique, so the order is total without a
    tiebreaker).
  - The cursor is opaque base64url JSON `{"v":1,"id":"…"}`. A forged or invalid cursor → 422.
  - `limit` is 1–100 (default 50). `next_cursor` is `null` on the last page.
- **Consequences**: stable pages under concurrent inserts and O(log n) seeks. There is no total count and no jumping
  to page N, which the UI does not need. Phase 2 lists (`organizations`, `members`) keep `{items}` (small and
  bounded). Errors keep the RFC 9457 problem format from Phase 1.

## ADR-023 — Python SDK and demo as separate uv projects
- **Status**: Accepted (Phase 3, 2026-10-17).
- **Decision**:
  - `packages/python-sdk` (`tracepilot_sdk`, hatchling, src layout, Python ≥ 3.11) and `demo/sample-service`
    are their own uv projects with their own lockfiles. The demo depends on the SDK through a `path` source.
  - The server's lockfile and Docker image do not include them.
  - Server ruff/mypy exclude them; CI runs ruff, format, mypy and pytest for both on Python 3.11 and 3.13.
  - `scripts/ci-local.sh` runs the same steps.
- **SDK retry policy**: retry only connect errors, timeouts, 429 (honouring `Retry-After`, capped) and 5xx.
  - At most `max_retries` (3) retries, with exponential backoff and full jitter.
  - 400/401/403/413/422 never retry.
  - Batches are split by count (≤ 500) and by serialized size (below 1 MiB).
  - Because event ids are idempotent, retrying after an ambiguous timeout cannot duplicate rows.
- **Demo determinism**:
  - Event ids are `uuid5(NAMESPACE, "{scenario}:{seed}:{index}")`.
  - All other fields come from `random.Random("tracepilot-demo:{scenario}:{seed}")`, plus evenly spaced timestamps
    from `--start`.
  - Every event is tagged `environment="demo"` and `attributes.synthetic=true`.

## ADR-024: Log search uses PostgreSQL full-text search (prefix tsquery), not pg_trgm (2026-10-17)

**Context.** The log explorer needs text search on `message`. Options: `ILIKE '%x%'` (seq scan),
`pg_trgm` GIN (substring and fuzzy matching; needs an extension and a large index), or
full-text search on the generated `message_tsv` column (`to_tsvector('simple', message)`), which
already has a GIN index from Phase 3.

**Decision.** Use FTS with the `simple` configuration (no stemming or stop words, so `500`,
`db-primary` and identifiers stay searchable). The server tokenises `q` itself: Unicode `\w+`
runs, lower-cased, at most **8 tokens of up to 64 characters**, joined as `tok:* & tok2:*`
(every word, prefix match). `q` is capped at **200 characters** (422 above that). The tsquery
string is a bound parameter given to `to_tsquery('simple', :q)`, and because only `\w` characters
survive, tsquery operators typed by a user (`& | ! ( ) : * '`) can never cause a syntax error or
change the query's meaning. Input that contains only punctuation means "no text filter".

**Consequences.** No new extension. The existing GIN index serves the search, and the planner
can also walk the `(project_id, ts, id)` index and filter. Substring matches in the middle of a
word (`imeou` -> `timeout`) are not supported, and neither is fuzzy matching. If users need
them, add `pg_trgm` with a GIN index on `message` in a later migration and keep this API
contract. Tests: `tests/test_query_unit.py::test_search_tsquery`,
`tests/test_logs_api.py::test_search_escaping_never_errors`.

## ADR-025: Log keyset pagination, time-range rules and list/detail split (2026-10-17)

**Decision.**
- Order is `ts DESC, id DESC`. The cursor is base64url JSON `{"v":1,"ts":"<iso>","id":<int>}`
  and the next page is `WHERE (ts, id) < (:ts, :id)`. Limit defaults to 50 (1..200, 422
  otherwise). The server fetches `limit + 1` rows, so `next_cursor` is null exactly when no rows
  remain. The envelope is `{items, next_cursor, range_from, range_to}`, the same as ADR-022 plus
  the resolved range. A malformed cursor (bad base64 or JSON, unknown version, naive timestamp,
  non-positive or non-integer id) is a 422 Problem response and never a 500.
- Rows ingested later with newer timestamps never shift later pages. Rows with older timestamps
  inserted mid-walk can appear on later pages, as is normal for keyset pagination.
- Time range: half-open `[from, to)`, aware datetimes only, default last 24 h, at most 30 days,
  and `from < to`. One helper (`app/core/time_range.py`) is shared by logs, facets and metrics.
- List items omit `attributes` (which can be up to 16 KB each). `GET /logs/{id}` returns the full
  event including `attributes` and `received_at`. Facets (levels / top 100 services / top 20
  environments with counts) are separate, bounded GROUP BY queries.
- `log_id` is the BIGINT identity, scoped by `project_id` in the WHERE clause, so an id from
  another project returns 404.

## ADR-026: Metric definitions, null semantics and on-request aggregation (2026-10-17)

**Decision.** The definitions in `docs/METRICS.md` are normative. In short:
- request = `http_status IS NOT NULL`
- error = level error/critical OR status >= 500
- error rate = request_errors / requests
- percentiles = `percentile_cont` over requests that have `duration_ms`

The predicates are defined once as SQLAlchemy expressions (`IS_REQUEST`, `IS_ERROR`,
`IS_SAMPLE`). A metric that cannot be computed honestly is `null` with a machine-readable reason:
- `no_requests`
- `no_latency_samples`
- `insufficient_samples`, when below 20 samples for summary metrics or 5 per time bucket

Time series are zero-filled with `generate_series` over `date_bin` buckets (1m/5m/15m/1h/6h/1d,
≤ 200 buckets). Org metrics cover non-archived projects only. Archived projects are still
readable on their own pages.

**Why not rollups now.** On-request aggregation with a covering index (ADR-027) answers 24 h
in under 10 ms and 7 d in about 100 ms at 150k rows per project (docs/PERFORMANCE.md).
Rollups add write amplification and the risk of staleness. They are deferred until data volume
requires them.

## ADR-027: Log query indexes (2026-10-17)

**Decision.** Migration 0004 makes three changes, measured in docs/PERFORMANCE.md:
- It replaces `(project_id, ts DESC)` with `(project_id, ts DESC, id DESC) INCLUDE (level,
  service, environment, http_status, duration_ms)`. This index serves the keyset order without
  a sort, and it serves the metric aggregates and facets as index-only scans.
- It adds a partial index on `(project_id, request_id)`.
- It adds a partial "errors" index on `(project_id, ts DESC, id DESC)`.

The extra write cost on ingest is accepted: there are 3 more index entries per row, about 22 MB
of index at 150k rows.

## ADR-028: Organization rename (2026-10-17)

`PATCH /organizations/{org_id}` with `{name}` (1–100 characters, whitespace collapsed, extra
fields rejected) is allowed for owner and admin only (`ORG_UPDATE`). The slug stays the same, so
links stay valid. A change writes an `org.updated` audit entry with `{name: {from, to}}`. A no-op
rename writes nothing.
