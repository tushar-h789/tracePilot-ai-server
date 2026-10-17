# TracePilot AI — Testing Strategy

> Status: Phase 0 plan. Test IDs map to FR/NFR ids in [PROJECT_PLAN.md](PROJECT_PLAN.md) and threats T1–T21 in
> [SECURITY.md](SECURITY.md).

## 1. Test pyramid

```
                 ┌──────────────┐
                 │ Playwright   │  few: critical journeys + authz negatives (client repo / E2E workflow)
               ┌─┴──────────────┴─┐
               │ API tests (HTTPX) │ many: every endpoint, RBAC matrix, cross-tenant, CSRF, limits
             ┌─┴──────────────────┴─┐
             │ Integration (real PG,│ repositories, migrations, detection SQL, retention, Celery tasks (eager)
             │ Redis via Docker)    │
           ┌─┴──────────────────────┴─┐
           │ Unit (pure Python / TS)   │ most: services, redaction, evidence validation, schemas, key/token utils,
           │                            │ React components & hooks
           └────────────────────────────┘
```

## 2. Backend (server repo) — Pytest

| Layer | Scope | Tooling |
|---|---|---|
| Unit | Services with fake repositories, redaction rules, evidence validator, fingerprinting, token/CSRF/key helpers, Pydantic schemas (incl. `InvestigationAnalysis` limits), rule evaluation math | pytest, pytest-randomly (optional), hypothesis (optional for redaction/limits) |
| Integration | Repositories + migrations against **real PostgreSQL 18** and **Redis 8** in Docker (Compose locally, service containers in CI); `alembic upgrade head` + downgrade smoke; FTS and percentile queries; partial unique index behaviour; retention batches | pytest fixtures with per-test transaction rollback / truncate; no SQLite substitutes |
| API | FastAPI app via `httpx.AsyncClient`/`ASGITransport` (or `TestClient`), real DB; cookies + CSRF flow end-to-end | pytest + HTTPX |
| Worker | Celery tasks executed eagerly or with an in-process worker; detection and investigation tasks with fake provider | pytest |
| AI contract | Shared contract test suite run against every provider: fake (always, CI), Ollama (local/opt-in marker `@pytest.mark.ollama`), Gemini (P8, opt-in) | pytest markers |

## 3. Frontend (client repo)

| Layer | Scope | Tooling |
|---|---|---|
| Unit/component | Form schemas (Zod), RHF forms (validation, error display), tables, filters, chart wrappers (render + accessible table fallback), API client wrappers with mocked fetch | Vitest + React Testing Library + jsdom; MSW optional |
| Type contract | Generated OpenAPI types compile; CI regenerates from the server schema and fails on drift | `openapi-typescript` + `tsc --noEmit` |
| E2E | Register → create org → create project → create key (shown once) → generator ingests → logs explorer filter/search → incident appears → acknowledge → run investigation (fake provider in CI) → resolve. Negative: viewer cannot see key-create button **and** direct API call returns 403; user of org B gets 404 on org A URLs. Accessibility checks with `@axe-core/playwright` on main pages. | Playwright against full Docker Compose stack |

## 4. Security and authorization tests (CI-blocking)

- **AuthN/session (T1–T3)**: correct/incorrect login, generic errors identical for unknown email vs wrong password,
  rate limit → 429, refresh rotation, refresh reuse → family revoked, logout revokes, expired/tampered/`alg=none`
  token rejected, cookie attributes asserted (HttpOnly, SameSite, Secure in prod settings, Path).
- **CSRF (T5)**: every unsafe cookie-authenticated route rejects missing/mismatched `X-CSRF-Token` and foreign
  `Origin`. Implemented as a parametrized test over the app's route table, so new routes are covered automatically.
- **RBAC matrix (T18)**: table-driven test generated from PROJECT_PLAN §5: for each (role, capability) assert
  allowed (2xx) or 403. Last-owner protection tests.
- **Cross-tenant (T10)**: fixture with two orgs (A, B) each with projects, keys, logs, incidents, investigations.
  For every endpoint group (orgs, members, projects, keys, logs, metrics, incidents, rules, investigations) a user
  of B requesting A's resource ids gets **404** and no data; list endpoints never include A's rows; ingestion key of
  project A cannot write into project B. Also parametrized over the route table to catch new endpoints.
- **Ingestion abuse (T4, T8, T15)**: missing/invalid/revoked key → 401; key on user API → 401; cookie on ingest →
  401; > 1 MiB → 413; > 500 events / oversized fields → 422; rate limit → 429; idempotent replay and 409 on hash
  mismatch.
- **Injection (T7)**: SQL meta-characters and tsquery syntax in `q`, sort and filter params.
- **AI (T13, T17, T19)**: fixtures containing prompt-injection strings and fake ids (`L999`, `M0`, `../`), fake
  provider returning invalid ids/oversized fields/non-JSON → validator drops ids, flags unsupported, or marks
  `failed`; assert incident status never changes as a result of an investigation; redaction tests assert secrets
  from fixtures never appear in the provider input (captured by the fake provider).
- **Information disclosure (T11, T14)**: log-capture test asserts passwords, cookies, Authorization headers and
  key secrets never appear in application logs; prod settings hide docs and stack traces.

## 5. AI provider tests

- **Fake provider** (deterministic): returns scripted analyses, errors, timeouts; records received context for
  assertions. Used in all CI tests and E2E.
- **Contract suite** (same tests for every provider): output validates against `InvestigationAnalysis`;
  respects limits; returns model id and duration; handles timeout (raises `ProviderTimeout`), unavailable
  endpoint (`ProviderUnavailable`), invalid output (`ProviderInvalidOutput`).
- **Ollama acceptance (P6 gate, local, opt-in marker)**: for ≥ 3 generator scenarios (error spike in one
  service, latency regression on one path, downstream dependency errors) — 100 % schema-valid results after
  retries, 100 % of *displayed* evidence ids valid, ≥ 1 hypothesis citing evidence from the faulty service in
  ≥ 2 of 3 scenarios (quality smoke, not a benchmark), each run < 180 s on the dev laptop. Results recorded in
  PROGRESS.md with model id.
- Gemini (P8) must pass the same contract and acceptance suites before being selectable.

## 6. Static analysis and supply-chain checks

| Check | Server | Client |
|---|---|---|
| Lint/format | ruff (incl. `S` bandit-derived rules), ruff format | ESLint (Next.js config), Prettier |
| Types | mypy (strict on `app/services`, `app/schemas`, `app/infrastructure/ai`) | `tsc --noEmit` (strict) |
| SAST-lite | bandit | eslint-plugin-security (optional) |
| Dependencies | `pip-audit` (on exported uv lock) | `bun audit --audit-level=high` (triaged ignores documented) |
| Secrets | gitleaks (GitHub Action or CLI; free for public repos/personal use — verify licence terms in P1) | same |
| Containers | Hadolint for Dockerfiles; optional Trivy image scan (free, OSS) | — |

## 7. CI matrix (GitHub Actions, least privilege)

All workflows: `permissions: contents: read`; triggers on `pull_request` and `push` to `main` and `phase-*`
branches; concurrency cancels superseded runs; caches for uv/bun.

**Server repo**
| Job | Runs on | Details |
|---|---|---|
| `lint-type` | ubuntu-latest, Python 3.13 | ruff, ruff format --check, mypy, bandit |
| `test` | ubuntu-latest, Python 3.13 | service containers `postgres:18`, `redis:8`; `alembic upgrade head`; pytest with coverage; fake AI provider |
| `audit` | ubuntu-latest | pip-audit, gitleaks, hadolint |
| `openapi` | ubuntu-latest | export `openapi.json` as artifact (consumed by client drift check) |
| `e2e` (P7) | ubuntu-latest | checkout server + public client repo, `docker compose --profile web up`, run Playwright, upload report |
| `py-next` (optional, non-blocking) | Python 3.14 | early warning for the 3.14 migration |

**Client repo**
| Job | Details |
|---|---|
| `lint-type` | Bun 1.3: `bun install --frozen-lockfile`, eslint, `tsc --noEmit` |
| `test` | Vitest + RTL with coverage |
| `build` | `next build` |
| `contract` | fetch server `openapi.json` (from server repo default branch raw file or artifact), regenerate types, `git diff --exit-code` |
| `audit` | `bun audit --audit-level=high`, gitleaks |

Ollama is **not** run in CI (CPU time, model download); the fake provider covers CI.

## 8. Coverage targets

| Area | Target |
|---|---|
| Server `services/`, `repositories/`, `infrastructure/ai/`, security helpers | ≥ 85 % lines, ≥ 75 % branches |
| Server overall | ≥ 80 % lines (CI fails below) |
| Client `lib/` + form/components logic | ≥ 70 % lines |
| Authz | 100 % of routes covered by the parametrized CSRF and cross-tenant tests (enforced by a route-table check) |

Coverage is a floor, not a goal; security tests are required regardless of percentages.

## 9. Test data

- Factories for users/orgs/projects/keys; the synthetic generator's scenario library reused for detection and AI
  tests (seeded RNG for determinism).
- No real customer data, ever. Fixtures with "secrets" use obviously fake values (e.g. `FAKE-NOT-A-SECRET-0000`) and are
  allowlisted in gitleaks config.

## 10. Phase 1 implementation status (2026-10-17)

### How to run (server)
```bash
docker compose up -d postgres redis          # integration tests need real services
./scripts/ci-local.sh                        # exactly the CI steps (reads TEST_* URLs from .env)
uv run pytest -m "not integration"           # unit tests only, no services
```
Integration tests are marked `@pytest.mark.integration` and use `TEST_DATABASE_URL` (database name must end in
`_test`; created automatically on first Postgres start by `docker/postgres/initdb/10-create-test-db.sh`) and
`TEST_REDIS_URL` (DB 15). Without those variables they are skipped, unless `TP_REQUIRE_INTEGRATION=1` (CI and
ci-local) which turns a skip into a failure.

### Server tests in Phase 1 (46 tests: 41 unit + 5 integration)
| File | What it proves |
|---|---|
| `test_config.py` | valid defaults; CSV env parsing; invalid env/URL/origin/provider values fail fast; error text names the variable but never echoes the value; secrets masked in repr; prod refuses placeholder credentials, `*` hosts, eager Celery, DEBUG logs; docs disabled in prod |
| `test_health.py` | live 200 without dependencies; security headers + request id; readiness 503 with per-check `error` and no internal details when Postgres/Redis are unreachable; **integration**: 200 with real Postgres+Redis; 503 when only Redis is down |
| `test_http_security.py` | untrusted Host → 400; CORS allowlist; unhandled exception → generic problem+json without message/class name; OpenAPI/docs 404 in prod, served in dev |
| `test_logging.py` | JSON log fields; request id; sensitive keys redacted (nested) |
| `test_celery.py` | `ping` task eager → `pong`; JSON-only serializer (no pickle), empty beat schedule |
| `test_migrations.py` | single head; **integration**: upgrade head → downgrade base → upgrade head against the test DB |
| `test_db_session.py` | **integration**: `get_db` yields a working session |
| `test_openapi_contract.py` | committed `openapi/openapi.json` equals the app's schema |
| `test_ai_provider_protocol.py`, `test_demo_sample_service.py` | Protocol is structural; demo dry-run emits valid events |

### Client tests in Phase 1
Vitest + RTL (`tests/unit`): API client (success, http error with status/body, accepted 503, network, timeout,
invalid JSON, invalid `API_BASE_URL`), base URL config, `getSystemStatus` state mapping, system status widget
(ok/degraded/unreachable/skeleton), app shell (nav links, `aria-current`, skip link, mobile trigger), route error
boundary (no raw error text). Playwright smoke (`e2e/smoke.spec.ts`): login renders, `/` → `/overview` in the app
shell with a real status state, 404 page.

### Bruno
`bruno/` collection (OpenCollection YAML) with assertions + tests per request; run headlessly with
`cd bruno && bun x @usebruno/cli@4.2.1 run --env Local` against a running API (see `bruno/README.md`).

## 11. Phase 2 implementation status (2026-10-17)

### Server: 182 tests (106 unit + 76 integration), coverage 97.86 % (gate 80 %)
| File | Tests | What it proves |
|---|---:|---|
| `test_identity_unit.py` | 57 | Argon2id params/verify/rehash/dummy verify; password policy (length, common list, equals email); email normalisation; token hashing; CSRF sign/verify/binding/tamper; trusted-proxy IP resolution; full role × permission matrix and every `check_can_*` transition rule |
| `test_auth_api.py` | 29 | register/login/me/logout flow; generic duplicate registration; no password echo; generic invalid credentials; inactive user; rehash on login; hashed session storage; absolute + idle expiry; touch interval; revocation (one/all/current); fixation; CSRF missing/mismatch/missing-cookie/pre-login token/foreign Origin; rate limits per email/IP/register + `Retry-After`; Redis down → 503 |
| `test_organizations_api.py` | 42 | create/list/get; mass assignment rejected; auth required; **cross-tenant 404** on every org route; add/change/remove matrices for every actor role × target role; last-owner 409; input validation; case-insensitive email; audit log access + contents; atomic create and rollback; service-level denial audited without a lock wait |
| `test_log_redaction.py` | 2 | redaction of auth keys; a captured full auth flow contains no password, session token or CSRF token |
| updated `test_config.py`, `test_http_security.py` | — | prod guards for `SECRET_KEY`, `COOKIE_SECURE`, Argon2 memory floor |

Integration tests use a migrated `_test` database (Alembic `upgrade head` once per session) and `TRUNCATE` all
tables plus delete `rl:*` keys before each test. Test settings use cheap Argon2 (t=1, m=1 MiB, p=1) for speed;
the production parameters are unit-tested separately.

### Client: 59 Vitest tests (9 files), coverage 85.5 % statements / 82.3 % branches
New in Phase 2: `browser-api.test.ts` (credentials include, CSRF header on unsafe methods only, token fetched
when the cookie is missing, exactly one retry on `csrf_failed`, nothing written to web storage, field-error
mapping), `auth-guards.test.ts` (proxy redirect rules, server-side `getCurrentUser` forwards only the session
cookie, client role matrix mirrors the server), `auth-forms.test.tsx` (accessible Zod validation, generic 401
message, 429 message, safe `next` redirect, password policy, register → sign in), `members-manager.test.tsx`
(admin vs member controls, server refusal shown, not-found state, empty/create, error with retry).

### Playwright E2E against the real API (5 tests)
`e2e/auth.spec.ts` runs register → wrong password (generic error) → login → create org → reload and see it →
members page shows "Owner" → logout → `/orgs` redirects to `/login?next=%2Forgs` → the old cookie gets 401 from
`/api/v1/auth/me`; it asserts `localStorage`/`sessionStorage` hold no token-like values and, via
`context.cookies()`, that `tp_session` is HttpOnly + SameSite=Lax + Path=/ while `tp_csrf` is readable. A second
test proves a stale cookie is rejected by the server-side check. Requires the API stack:
```bash
cd ../tracePilot-ai-server && docker compose up -d postgres redis api && docker compose run --rm migrate
cd ../tracePilot-ai-client && bun run build && bun run test:e2e
```
CI does the same in the client's `e2e` job (checks out the matching server branch, starts the stack with
throwaway credentials).

### Bruno (28 requests, 32 tests)
Folders `auth`, `organizations`, `organization-members`, `negative-cases`; each run creates unique users
(`runId`), so it can be re-run against the same database. Run with
`cd bruno && bunx @usebruno/cli run --env Local`.


## 12. Phase 3 implementation status (2026-10-17)

### Server: 298 tests (134 unit + 164 integration, real PostgreSQL 18 + Redis 8), coverage 97.41 % (gate 80 %)
New files, 117 tests:
- `test_projects_api.py`: CRUD, role matrix, cross-org 404, slug per org, archive/restore, keyset pagination,
  audit, auth/CSRF.
- `test_ingestion_keys_api.py`: secret shown once and only the hash stored, permission matrix, revoke, rotate with
  and without grace, active-key cap, foreign key ids 404, audit without secrets, sessions and keys kept separate.
- `test_ingest_api.py`: rows written, UTC normalization, duplicates within and across batches, per-project scope,
  foreign `project_id`, generic 401s, cookie-only 401, archived project, 413 (header and streamed), 422 (too many,
  invalid, no echo), malformed, gzip 415, redaction, 429 per key and per project, Redis down 503, `last_used_at`
  throttle, CSRF/CORS, no key material in logs.
- `test_ingest_unit.py`: key entropy and format, HMAC, bearer parsing, cursors, event contract, scrubbing,
  permission matrix.

The migration test also covers `0003` upgrade → downgrade → upgrade, and CI runs `alembic check`.

### SDK (`packages/python-sdk`): 23 tests, coverage 95.87 % (gate 90 %)
Mocked HTTP via `httpx.MockTransport`. Covers:
- the request contract;
- retries (503, connect error, timeout) and no retry on 4xx;
- 429 + `Retry-After`, including give-up and an excessive value;
- persistent 5xx, backoff cap;
- chunking by count and by bytes;
- local validation;
- env configuration, redacted `repr`, and the key never appearing in errors or logs.

### Demo (`demo/sample-service`): 30 tests, coverage 97.70 % (gate 85 %)
- The same seed gives byte-identical output for all six scenarios.
- Different seeds give disjoint ids.
- uuid5 ids do not depend on `--start`.
- Demo tagging and window bounds hold.
- Scenario shapes hold: error rates, latency medians and p95, error types and statuses, recovery trend.
- CLI: dry-run, missing key → exit 2, bad arguments.
- Sending through the SDK against a mocked API, including an idempotent rerun and keeping the key out of output.

Both the SDK and the demo run on Python 3.11 and 3.13 in CI (`sdk-demo` matrix).

### End-to-end acceptance: `scripts/e2e-demo.sh` (compose stack)
1. Creates a run-unique user, org, project and key through the API.
2. Sends all six scenarios (seed 42, 200 events each, fixed `--start`).
3. Counts rows with `docker compose exec postgres psql`.
4. Reruns with the same seed and checks the counts are unchanged.
5. Revokes the key and checks a further run fails (401) and stores nothing.

Result on 2026-10-17: **1200 rows after pass 1, 1200 after the rerun** (each run reported `duplicates: 200` per
scenario); the revoked key was rejected.

### Bruno: 65 requests, 70 tests, 69 assertions, all passing (rerunnable)
New folders: `projects/` (12), `ingestion-keys/` (8), `ingest/` (12), plus 5 cross-project negatives.

### Client
Vitest: 84 tests. Playwright: 6 E2E tests, including `projects.spec.ts`. See the client repo `docs/PROGRESS.md`.
