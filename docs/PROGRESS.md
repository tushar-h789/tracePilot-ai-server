# TracePilot AI — Progress Log

## Phase 0 — Discovery, requirements, architecture (documentation only)

- **Date**: 2026-10-17 (Asia/Dhaka, UTC+6)
- **Status**: Complete — pending user review of the docs.
- **Branch**: `phase-0/discovery` (both repos). No application code, no `pyproject.toml`, no `package.json`.

### Deliverables
| File | Content |
|---|---|
| `docs/PROJECT_PLAN.md` | Vision, personas, FRs, NFRs, RBAC matrix, out-of-scope, phase plan P0–P8 with acceptance criteria, cut list, risks |
| `docs/ARCHITECTURE.md` | C4 context/container diagrams, module layout, DI, API boundaries, data flows & trust boundaries, AI provider contract, DB schema, tenant isolation, retention, local dev, versions table with sources |
| `docs/SECURITY.md` | Assets, actors, STRIDE table, controls per area, AI-specific risks, redaction rules, residual risks |
| `docs/TESTING.md` | Test pyramid, backend/frontend/security/AI tests, static & supply-chain checks, CI matrix, coverage targets |
| `docs/DECISIONS.md` | ADR-001 … ADR-015 |
| `README.md`, `.gitignore` | Repo entry point and ignore rules |
| Client repo | `README.md`, `.gitignore`, `docs/PROGRESS.md` (client notes; docs canonical here) |

### Environment facts (verified on the dev machine)
- Ubuntu 26.04.1 LTS, kernel 7.0.0-38-generic, x86_64; 16 CPU threads; 26 GiB RAM (~7–8 GiB available at check
  time); disk 468 G total / 296 G free.
- GPU: AMD Radeon Vega integrated (Cezanne), **no NVIDIA** → Ollama CPU-only.
- git 2.53.0; Python 3.14.4 (system only; no 3.12/3.13 installed); uv 0.11.24; no pip3; Node v24.18.0; npm 11.16.0;
  bun 1.3.14; no pnpm; Docker 29.9.1; Docker Compose v5.6.0; psql 18.6; gh 2.78 (logged in as tushar-h789, ssh).
- Listening ports: 127.0.0.1:5432 (host Postgres), 127.0.0.1:3000, *:3001 → stack uses 8000 (API), 3100 (web),
  5433 (Postgres), 6380 (Redis), Ollama unpublished. Ports 3100/8000 confirmed free.
- No redis-server, no ollama on the host → both run in Docker.
- `/home/tushar` is itself a git repository (unrelated remote) → each project folder initialised as its own repo;
  nothing outside the two project folders was modified.
- Both GitHub repos are public and were empty before this push.

### Key decisions (see DECISIONS.md)
- Two-repo split with OpenAPI contract (ADR-001, ADR-012); modular monolith + Celery worker/beat (ADR-002).
- **Python 3.13** because Celery 5.6.x officially supports up to 3.13 (ADR-003). uv + npm (ADR-004).
- Cookie sessions + refresh rotation + CSRF double-submit (ADR-005); Postgres FTS (ADR-006).
- Ollama first with `qwen2.5:3b` default, provider Protocol, Gemini deferred to P8 opt-in (ADR-008, ADR-010).

### Commands executed in Phase 0 (all non-destructive)
Environment checks (user machine): `ls -la`, `git -C tracePilot-ai-server rev-parse --show-toplevel`
(→ `/home/tushar`, confirming the hazard before init), `git config --global user.name/user.email`,
`python3 --version`, `uv --version`, `node -v`, `docker --version`, `docker compose version`, `free -g`,
`ss -ltn`, `nproc`, `df -h`, `uname -r`, `/etc/os-release`, `gh repo view … --json visibility`,
`git ls-remote <both remotes>` (0 refs = empty).
Version research: PyPI JSON API, npm registry, Docker Hub tags API, GitHub releases API, Ollama docs/library,
Gemini pricing page, Celery README/changelog, shadcn docs (see ARCHITECTURE §11).
Git (per repo, inside the project folder only): `git init -b main`, `git rev-parse --show-toplevel` (must equal the
folder), `git checkout -b phase-0/discovery`, `git add` of the listed files, review `git status`/`git diff --cached`,
secret grep on staged content, `git commit`, `git remote add origin <ssh url>`, `git push -u origin phase-0/discovery`.
`main` is not pushed; the remote default branch becomes `phase-0/discovery` because the repos were empty.

### Not verified in Phase 0 (scheduled)
Docker `node:24` slim tag (P1) · Next.js + TypeScript 7 compatibility (P1) · gitleaks licence/action choice (P1) ·
Ollama per-model resident RAM and latency (P6) · Gemini free-tier rate limits (P8).

### Next steps (Phase 1 — scaffolding)
1. Review/approve Phase 0 docs; optionally set `main` as default branch after first merge.
2. Server: `uv python install 3.13`, `uv init`, add pinned deps, app skeleton per ARCHITECTURE §4, health
   endpoints, settings, structured logging, Alembic baseline, Celery app/beat skeleton, Dockerfile (non-root),
   `docker-compose.yml`, `.env.example` (placeholders only), CI workflows.
3. Client: `create-next-app` (TS strict, App Router, Tailwind v4), shadcn init, Vitest/RTL, Playwright, OpenAPI type
   generation script, CI workflow.
4. P1 acceptance: `docker compose up` healthy, CI green in both repos.

---

## Phase 1 — Repository setup & development foundation

- **Date**: 2026-10-17 (Asia/Dhaka, UTC+6)
- **Status**: Complete locally — all checks green on the dev machine; GitHub Actions not yet observed (first run
  happens on push).
- **Branch**: `phase-1/foundation` (both repos, created from `phase-0/discovery`). `main` does not exist on the
  remotes; no PR opened (see "Decisions needed").
- **Scope**: foundation only — no authentication, tenancy or business features.

### Delivered (server)
- uv project: `pyproject.toml`, `uv.lock`, `.python-version` (3.13; uv-managed CPython 3.13.14).
- App skeleton (`app/`): factory, settings, JSON logging, security middleware, DB session/Base, health
  schemas/repository/service/routes, Celery app + `tracepilot.ping` + empty beat schedule, AI provider Protocol
  placeholder. Layout in ARCHITECTURE.md §4 ("Phase 1 implemented layout").
- Endpoints: `GET /api/v1/health/live`, `GET /api/v1/health/ready` (200 / 503, per-check status + latency).
- Alembic with settings-driven URL; empty baseline `0001_baseline`.
- `Dockerfile` (multi-stage, uv, python:3.13-slim, non-root uid 10001, healthcheck), `docker-compose.yml`
  (postgres:18, redis:8, api, worker, beat, `migrate` profile, `web` profile), `.env.example`.
- `openapi/openapi.json` + `scripts/export_openapi.py` (`--check` for drift); `scripts/ci-local.sh`.
- `.github/workflows/ci.yml`: lint-type, test (Postgres 18 + Redis 8 services, alembic up/down/check),
  contract (OpenAPI drift + pip-audit), docker (hadolint, gitleaks, build, non-root check), py314 (non-blocking).
- `bruno/` collection (OpenCollection YAML) with `health/live` + `health/ready`.
- `demo/sample-service/` dry-run synthetic log emitter.
- Docs: README (setup/ports/migrations/troubleshooting), ADR-004 revised (Bun), ADR-014 accepted, ADR-016/017
  added, ARCHITECTURE/TESTING/SECURITY Phase 1 sections.

### Verification results (actually executed on the dev machine)
| Check | Result |
|---|---|
| `docker compose up -d` | postgres, redis, api, worker, beat → all `healthy` |
| `docker compose run --rm migrate` | `Running upgrade -> 0001_baseline` |
| `alembic current` / `history` (container and host) | `0001_baseline (head)` |
| `curl /health/live` | 200 `{"status":"ok"}` |
| `curl /health/ready` | 200 `{"status":"ok","checks":{"database":{"status":"ok",…},"redis":{"status":"ok",…}}}` |
| `docker compose stop redis` → ready | **503** `{"status":"unavailable", … "redis":{"status":"error"}}`; live still 200 |
| `docker compose start redis` → ready | 200 again; worker reconnected automatically |
| Celery `celery call tracepilot.ping` | worker log: `Task tracepilot.ping[…] succeeded … 'pong'` |
| ruff check / ruff format --check | pass / 54 files formatted |
| mypy (strict, pydantic plugin) | `Success: no issues found in 45 source files` |
| bandit -r app | 0 issues (549 LOC) |
| pytest (unit + integration, real PG18/Redis8) | **46 passed**, coverage **97.93 %** (gate 80 %); 1 warning (Starlette TestClient → httpx2 deprecation) |
| alembic upgrade → downgrade base → upgrade → check (test DB) | pass; `No new upgrade operations detected` |
| OpenAPI drift | up to date |
| pip-audit (all groups, hashed export) | `No known vulnerabilities found` |
| hadolint (both Dockerfiles) | clean |
| gitleaks (working tree) | only `.env` (untracked, gitignored, real local password) flagged — expected |
| Server image | `tracepilot-server:dev` 339 MB |
| Web image via `docker compose --profile web build web` | built in 109 s, 415 MB; container healthy, uid 1000, `/overview` showed "All systems ready" via `http://api:8000` |
| Bruno CLI 4.2.1 `run --env Local` | 2/2 requests, 4/4 tests, 4/4 assertions — PASS |
| RAM (docker stats) | api 69 MB, worker 63 MB, beat 46 MB, postgres 56 MB, redis 6 MB, web 60 MB |

Client results are recorded in the client repo's `docs/PROGRESS.md`.

### Known limitations / follow-ups
- GitHub Actions workflows were validated by running the same steps locally only.
- redis-py pinned at 6.4.0 by kombu; TypeScript at 5.9 (ADR-016).
- Postgres/Redis host ports are published to loopback for host tooling (ADR-017).
- No CSP on the web app yet (P7); Redis password visible in container process list.
- `ollama` service intentionally absent until Phase 6.

### Decisions needed from the user
- `main` does not exist on either remote (default branch is `phase-0/discovery`). Decide whether to create
  `main` from `phase-0/discovery` (or from `phase-1/foundation` after review) and make it the default branch;
  then open PRs `phase-1/foundation → main` in both repos.

### Next (Phase 2 — auth, orgs, RBAC)
Users/sessions/refresh rotation, Argon2id, cookies + CSRF double-submit, login rate limiting, orgs/memberships,
RBAC dependencies, Bruno `auth/` and `organizations/` folders, client login/register pages wired to the API via
the Next.js `/api/v1` proxy.

## Phase 2 — Identity, access control and database foundation (2026-10-17)

Branch `phase-2/identity-access` (stacked on `phase-1/foundation`; the PR targets `phase-1/foundation` and will be
retargeted to `main` after the Phase 1 PR merges). Same branch name in the client repo.

### Delivered (server)
- **Schema** (migration `0002_identity_access`): `users`, `sessions`, `organizations`, `organization_members`,
  `audit_logs` with check constraints, FKs and indexes (ARCHITECTURE §9a).
- **Auth**: register (202, generic), login, logout, logout-all, me, session list/revoke; Argon2id with explicit
  parameters and rehash on login; opaque hashed session tokens with idle + absolute expiry (ADR-018); signed,
  session-bound CSRF double-submit + Origin check; Redis rate limits per email/IP with 429 + `Retry-After`,
  failing closed (ADR-019); password policy 12–128 + common-password list; dummy-hash timing for unknown emails.
- **Organizations**: create (atomic, creator = owner), list, get, members list/add/change role/remove, audit log;
  RBAC matrix in `app/core/permissions.py` (viewer kept, ADR-020); 404 for non-members, 403 for insufficient role,
  last-owner 409, no self-escalation, admins cannot touch owners; org row locked during member changes.
- **Reusable access pattern** for Phase 3 projects: `authorize_org` / `require_org_permission` dependencies.
- **Audit events** for auth, membership changes and sensitive denials; log redaction extended (csrf, credential,
  password_hash) with a test that captures a whole auth flow.
- **OpenAPI** regenerated; drift check in tests and CI. **Bruno**: `auth`, `organizations`,
  `organization-members`, `negative-cases` folders.
- Compose/CI: `SECRET_KEY` required by the api service; `COOKIE_SECURE`, `TRUSTED_PROXIES` passed through.

### Delivered (client) — see the client repo's PROGRESS.md
Login/register (React Hook Form + Zod), cookie-only API client with automatic CSRF header, same-origin `/api/v1`
proxy, server-side auth check in the app layout, logout, organization list/create, members page with role-based
controls, loading/empty/error states, regenerated API types, Playwright E2E against the real API.

### Verification results (executed on the dev machine, 2026-10-17, Asia/Dhaka)
| Check | Result |
|---|---|
| `scripts/ci-local.sh` (ruff, ruff format, mypy, bandit, pytest+coverage, alembic up/down, OpenAPI drift, pip-audit, docker build) | all passed; peak 214 MB in a 2 GiB scope |
| pytest | 182 passed (106 unit + 76 integration), coverage 97.86 % |
| mypy / ruff / bandit | 0 issues in 76 source files / clean / 0 findings |
| Alembic | `upgrade head`, `downgrade base`, `upgrade head`, `alembic check` (no drift) — dev DB at `0002_identity_access` |
| pip-audit | no known vulnerabilities |
| hadolint (server + client Dockerfiles) | clean |
| Bruno CLI (`bunx @usebruno/cli run --env Local`) | 28/28 requests, 32/32 tests, 32/32 assertions passed |
| Client lint / typecheck / format / Vitest | clean / clean / clean / 59 passed, 85.5 % statements |
| Client `check:api` | `schema.d.ts` matches `openapi/openapi.json` |
| Client `bun audit --audit-level=high` | exit 0 (one triaged dev-only advisory ignored, as in Phase 1) |
| Client production build (1 worker) | passed, peak ≈ 0.56 GB |
| Playwright E2E vs real API | 5/5 passed (peak 615 MB incl. Next server + Chromium) |
| Web container (`docker compose --profile web build web`) + proxy smoke | built; register 202 → login 200 → me 200 through the container; anonymous `/orgs` → 307 `/login?next=%2Forgs` |
| gitleaks v8.30.1 (full history, both repos) | no leaks found (server 21 commits, client 17 commits) |

Memory rule: the machine already sat at ≈ 19.9 GB used of the 20.8 GB cap from other workloads, so heavy steps
ran one at a time in `systemd-run --user --scope -p MemoryMax=… -p MemorySwapMax=0`; the client build was
limited to one worker; worker/beat containers were stopped during testing. Highest system-wide "used" observed
during any step: 20.8 GB (web image build, within the cap).

### Known limitations
- No password reset, email verification or MFA (documented out of scope); no invitations — members are added by
  the email of an existing account (admins can therefore learn whether an email is registered, ADR-019).
- Members cannot leave an organization themselves; org rename/delete endpoints are not exposed yet (permissions
  exist in the matrix).
- Per-IP rate limits see the Next.js proxy's address for browser traffic (proxy not in `TRUSTED_PROXIES`).
- GitHub Actions results for this branch: see the PR checks.

### Next (Phase 3 — projects and ingestion keys)
`projects` table + `authorize_project` on top of `authorize_org`, project CRUD, hashed ingestion keys, Bruno
`projects/` and `ingestion-keys/` folders, client project pages.


## Phase 3: projects, ingestion and synthetic data (2026-10-17)

Committed directly to `main` (the user's instruction for this phase). Each push was preceded by a green
`scripts/ci-local.sh`.

### Delivered
- **Projects**: org-scoped CRUD with archive/restore, slug unique per org, keyset pagination, audit events.
  Cross-org access returns 404.
- **Ingestion keys** (ADR-021):
  - `tp_<id>_<256-bit>` keys, stored as HMAC-SHA256 with a pepper, plaintext returned once with `no-store`;
  - revoke, rotate with optional grace, cap of 10 active keys, admin+ to manage, audit events.
- **`POST /api/v1/ingest/logs`**:
  - authentication by bearer key only;
  - body cap before parsing; strict all-or-nothing contract; batch, timestamp and rate limits (per key and per
    project);
  - `ON CONFLICT DO NOTHING` idempotency per project;
  - redaction at ingest; fail closed without Redis.
- **Migration** `0003_projects_ingestion` (upgrade and downgrade tested). Pagination envelope (ADR-022). OpenAPI
  re-exported, with an `IngestionKey` security scheme.
- **`packages/python-sdk`** (`tracepilot_sdk`) and **`demo/sample-service`** (six deterministic scenarios, CLI)
  as separate uv projects (ADR-023), with CI on Python 3.11 and 3.13.
- **Bruno**: `projects/`, `ingestion-keys/`, `ingest/` and cross-project negatives.
- **`scripts/e2e-demo.sh`**: end-to-end acceptance against the compose stack.
- **Docs**: ADR-021/022/023, SECURITY §11, ARCHITECTURE §9e, TESTING §12, this section, READMEs.
- **Client** (separate repo): project pages, key management UI, Vitest and Playwright.

### Results (executed locally, 2026-10-17)

| Check | Result |
| --- | --- |
| ruff / ruff format / mypy (96 files) / bandit | clean |
| pytest (real Postgres 18 + Redis 8) | 298 passed (134 unit + 164 integration), coverage 97.41 %; one third-party deprecation warning (starlette testclient) |
| alembic upgrade → downgrade base → upgrade → check | passed |
| OpenAPI drift / pip-audit | up to date / no known vulnerabilities |
| SDK: ruff, mypy, pytest | 23 passed, 95.87 % (also 23 passed on Python 3.11) |
| Demo: ruff, mypy, pytest | 30 passed, 97.70 % (also 30 passed on Python 3.11) |
| gitleaks (full history) | no leaks, after fixing 3 false positives on fake key fixtures (see below) |
| docker build | image built |
| `scripts/e2e-demo.sh` (seed 42, 6 × 200 events) | pass 1: 1200 rows; same-seed rerun: 1200 rows (every scenario `accepted 0, duplicates 200`); revoked key → 401, rows unchanged |
| API logs during the e2e run | 0 full keys (regex grep); `ingest_auth_failed` logged with `key_prefix` only |
| Bruno CLI (`--env Local`) | 65/65 requests, 70/70 tests, 69/69 assertions; second run also green |

Rows per scenario after both passes (seed 42, `rows | errors`):

| Scenario | Rows | Errors |
| --- | --- | --- |
| db-timeout | 200 | 46 |
| external-api-failure | 200 | 45 |
| http-500 | 200 | 61 |
| latency | 200 | 0 |
| normal | 200 | 6 |
| recovery | 200 | 23 |

Every row has `is_synthetic=true` and `environment='demo'`.

### CI incident
The first push (`2dcbb08`) failed the gitleaks job. The `generic-api-key` rule matched three obviously fake
fixtures (`key = "tp_0123456789ab_" + "K" * 43`). The fix commit (`156e3b7`):
- builds the fixtures from pieces so the rule no longer matches;
- adds `.gitleaksignore` entries for the three historical fingerprints (history is not rewritten);
- adds the same gitleaks scan to `scripts/ci-local.sh`.

That run was fully green.

### Memory
The machine sat at about 20.6–21.4 GB used (of 27.4 GB; the 80 % cap is ≈ 21.9 GB) because of other workloads.
Every heavy step ran alone inside `systemd-run --user --scope -p MemoryMax=… -p MemorySwapMax=0`, after checking
`free -m`. Highest system-wide "used" observed:

| Step | Peak used |
| --- | --- |
| api image build | 21.0 GB |
| client build | 21.6 GB |
| Playwright E2E | 21.7 GB; one earlier attempt touched 21.9 GB, at the cap |
| pytest (scope) | < 0.25 GB |

The web container image was **not** rebuilt (the Docker build cannot be memory-capped and would exceed the cap).
The client E2E ran against a local production build on port 3100 while the web container was briefly stopped,
then restarted.

### Known limitations
- No logs explorer or query API yet (Phase 4). Rows can be inspected with `psql`.
- No pepper versioning: changing `INGEST_KEY_PEPPER` invalidates every key.
- Fixed-window rate limits. Idempotency is bounded by retention (the retention job is not implemented yet).
- The demo's default `--start` is "now", so reruns are identical only when `--start` is passed explicitly. Event
  ids are identical regardless, so reruns never duplicate.
- Rotation with a grace period is API-only; the UI rotates with grace 0.

### Next (Phase 4)
Log search/explorer API and UI on top of `log_events` (indexes for level/service/time/trace and full-text are
already in place), plus the retention job.
