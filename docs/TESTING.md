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
| Dependencies | `pip-audit` (on exported uv lock) | `npm audit --audit-level=high` |
| Secrets | gitleaks (GitHub Action or CLI; free for public repos/personal use — verify licence terms in P1) | same |
| Containers | Hadolint for Dockerfiles; optional Trivy image scan (free, OSS) | — |

## 7. CI matrix (GitHub Actions, least privilege)

All workflows: `permissions: contents: read`; triggers on `pull_request` and `push` to `main` and `phase-*`
branches; concurrency cancels superseded runs; caches for uv/npm.

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
| `lint-type` | Node 24: `npm ci`, eslint, `tsc --noEmit` |
| `test` | Vitest + RTL with coverage |
| `build` | `next build` |
| `contract` | fetch server `openapi.json` (from server repo default branch raw file or artifact), regenerate types, `git diff --exit-code` |
| `audit` | `npm audit --audit-level=high`, gitleaks |

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
