# TracePilot AI — Project Plan

> Status: Phase 0 (discovery) — documentation only. Canonical for the whole system (server + client).
> Related: [ARCHITECTURE](ARCHITECTURE.md) · [SECURITY](SECURITY.md) · [TESTING](TESTING.md) · [DECISIONS](DECISIONS.md) · [PROGRESS](PROGRESS.md)

## 1. Vision and scope

TracePilot AI is an AI-assisted application observability and incident investigation platform, built as a
**zero-budget, full-stack portfolio MVP in 6–8 weeks** by one developer. Applications send structured logs to a
project-scoped ingestion endpoint; TracePilot stores them, shows request volume / error rate / latency, detects
incidents with deterministic rules, and lets a user run a **background AI investigation** that summarizes probable
root causes with references to real evidence. AI assists humans; it never takes action on its own.

Repositories (two-repo split, see ADR-001):

| Repo | Contents |
|---|---|
| `tracePilot-ai-server` | FastAPI API, Celery worker + beat, Alembic migrations, `docker-compose.yml` for the full local stack, synthetic log generator / Python SDK (later), **canonical docs** |
| `tracePilot-ai-client` | Next.js web app (App Router), client-only notes |

## 2. Personas

- **Org owner**: creates the organization, manages billing-free settings, members and everything below.
- **Admin**: manages projects, ingestion keys, rules, members (except owners).
- **Member (engineer / on-call)**: explores logs, acknowledges/resolves incidents, runs AI investigations.
- **Viewer (stakeholder)**: read-only dashboards, logs, incidents.
- **Instrumented application** (machine actor): sends logs with a project ingestion key only.

## 3. Functional requirements (MVP)

IDs are referenced by phases and tests.

### Identity, tenancy, access
- **FR-01** Register with email + password; passwords hashed with Argon2id.
- **FR-02** Log in / log out; short-lived access token + revocable refresh session, both in HttpOnly cookies; refresh rotation; "log out" revokes the session server-side; "log out everywhere" revokes all sessions of the user.
- **FR-03** Login rate limiting (per IP and per account identifier) and generic error messages (no user enumeration on login/register/reset).
- **FR-04** Create organization; creator becomes `owner`. A user can belong to multiple organizations.
- **FR-05** Invite/add an existing user to an org by email with a role (MVP: direct add of registered users; email delivery out of scope). Change role, remove member. At least one owner must always remain.
- **FR-06** Server-enforced RBAC (owner/admin/member/viewer, matrix in §5) with strict tenant isolation: every query scoped by org/project; cross-org access returns 404.
- **FR-07** Org/project switcher in the UI; last selection remembered client-side (not trusted by the server).

### Projects and ingestion
- **FR-10** CRUD projects within an org (name, slug, environment label, retention days 1–30, default 7).
- **FR-11** Ingestion keys per project: create (secret shown **once**), list (prefix, name, created_by, created_at, last_used_at, revoked_at), revoke, rotate (create new + revoke old with optional overlap). Only a hash is stored.
- **FR-12** Batch ingestion endpoint authenticated **only** by an ingestion key; key never grants user API access and user cookies never authorize ingestion.
- **FR-13** Ingestion limits: max body size, max events per batch, max field sizes, per-key and per-project rate limits, `Idempotency-Key` support (replay returns the original result; same key + different payload returns 409).
- **FR-14** Synthetic log generator (CLI under the server repo) that produces realistic multi-service traffic with configurable error/latency scenarios (normal, error spike, latency regression, single-service outage) and sends it through the public ingestion API.

### Logs and metrics
- **FR-20** Logs explorer: filter by time range (required, bounded), level, service, HTTP status class, path, trace_id; full-text search on message; keyset (cursor) pagination; newest first; log detail drawer with attributes.
- **FR-21** Metrics for a project and optional service filter over a bounded window with bucket size: request volume, error rate (5xx and/or level≥error), latency p50/p95 (`duration_ms`).
- **FR-22** Overview dashboard: KPI cards + time-series charts (Recharts) + open incidents list.

### Incidents
- **FR-30** Incident rules per project (MVP types): `error_rate` (threshold %, window, min requests), `latency_p95` (threshold ms, window, min requests), `error_count` (count of level≥error per service in window). Default rules created with each project; owner/admin can edit/enable/disable.
- **FR-31** Scheduled detection (Celery beat, ~every 60 s) evaluates enabled rules; creates incidents with a deterministic **fingerprint**; dedups while an incident with the same fingerprint is open/acknowledged (updates `last_seen_at`, appends a timeline event instead of creating a duplicate).
- **FR-32** Incident list (filter by status/severity/service, paginated) and detail (rule, metric snapshot, related logs, timeline).
- **FR-33** Lifecycle: `open → acknowledged → resolved`; `open → resolved` allowed; resolved incidents are immutable except comments(optional). Every transition writes a timeline event with actor.

### AI investigation
- **FR-40** Member+ can start an investigation for an incident; runs asynchronously in the Celery worker; UI polls status (`queued → running → succeeded | failed`).
- **FR-41** Provider interface; `AI_PROVIDER=ollama` (local) is the first and default implementation; a fake provider is used in tests.
- **FR-42** Bounded, redacted context in; Pydantic-validated typed analysis out (`summary`, `probable_root_causes[{hypothesis, confidence, evidence_ids}]`, `recommended_next_steps`, `limitations`).
- **FR-43** Timeouts, bounded retries, and recording of provider, model id, duration, prompt/completion tokens (when reported), and failure reason.
- **FR-44** Server validates every `evidence_id` points to a real log/metric evidence item that was in the context for that incident/project; invalid references are dropped and flagged in `limitations`/validation report, never displayed as evidence.
- **FR-45** AI output is display-only data: never executed, never auto-resolves or changes incident state, rendered as escaped text (no HTML/Markdown injection).
- **FR-46** Graceful failure: provider down/timeout/invalid output → investigation `failed` with a user-safe message; the rest of the app keeps working.
- **FR-47 (later, P8)** Gemini free-tier provider behind the same interface, only after the Ollama provider passes the AI acceptance tests.

### Data lifecycle and operations
- **FR-50** Per-project log retention: scheduled, batched deletion of `log_events` older than `retention_days`; never deletes across projects; logs counts deleted.
- **FR-51** Project deletion by owner/admin with typed confirmation; data removed asynchronously in batches.
- **FR-52** Health endpoints: liveness (`/api/v1/health/live`) and readiness (`/api/v1/health/ready`: DB + Redis; AI provider reported separately and non-fatal).
- **FR-53** Basic org/team settings page and project settings page (incl. retention and keys).

## 4. Non-functional requirements

| ID | Area | Requirement |
|---|---|---|
| NFR-01 | Cost | Zero budget: only free/OSS software, local Docker, free GitHub Actions on public repos. No paid APIs or SaaS. |
| NFR-02 | Security | OWASP ASVS-inspired controls documented in SECURITY.md; no IDOR; secrets never logged; CSRF protection on cookie-authenticated state-changing requests. |
| NFR-03 | Tenancy | Every tenant-owned row carries `org_id` and/or `project_id`; repository methods require an authorized scope object. Cross-tenant tests mandatory in CI. |
| NFR-04 | Performance (local) | Ingest ≥ 500 events/s sustained on the dev laptop for batches of 500 (target, measured in P3/P7). Logs explorer p95 < 500 ms for a 24 h window over ~1 M rows (target, measured in P4/P7). |
| NFR-05 | AI resource bounds | CPU-only Ollama, small (~3–4B) quantized model; context capped (e.g. `num_ctx` 8192, ≤ 50 log lines, ≤ 6 kB serialized evidence); investigation timeout ≤ 180 s; worker AI concurrency 1. |
| NFR-06 | Reliability | Ingestion and UI work when the AI provider is unavailable; Celery tasks idempotent; bounded retries with backoff. |
| NFR-07 | Observability of itself | Structured JSON logs (request id, user id/org id where known, no secrets), health checks on every container. |
| NFR-08 | Accessibility | WCAG 2.2 AA intent: keyboard navigation, focus states, labels, color-contrast, charts with text alternatives/tables. Light theme default. |
| NFR-09 | Responsiveness | Usable from 360 px width; dense tables scroll horizontally. |
| NFR-10 | Maintainability | Layered backend (routes/schemas/services/repositories/models/infrastructure), DI, typed code (mypy/pyright for server, TS strict for client), lint + format in CI. |
| NFR-11 | Portability | `docker compose up` brings up the full local stack; no host Redis/Ollama required; no conflict with host Postgres (5432) or host ports 3000/3001. |
| NFR-12 | Containers | Non-root users, minimal slim images, health checks, DB/Redis/Ollama not published publicly (bound to 127.0.0.1 or internal network only). |
| NFR-13 | Supply chain | Lockfiles (`uv.lock`, `package-lock.json`), dependency audit in CI, secret scanning, least-privilege GitHub Actions permissions. |
| NFR-14 | Contract | Server OpenAPI schema is the API contract; client types are generated from it; CI detects drift. |

## 5. Roles and permissions matrix

Roles are **per organization** (membership). In the MVP, project access is inherited from the org role
(per-project roles are future work). "✓" = allowed; "—" = denied (server returns 403 for an in-tenant
action the role cannot perform; 404 for resources in other orgs).

| Capability | owner | admin | member | viewer |
|---|:-:|:-:|:-:|:-:|
| View org, projects, dashboards, logs, metrics, incidents, investigations | ✓ | ✓ | ✓ | ✓ |
| Update org name/settings | ✓ | ✓ | — | — |
| Delete organization | ✓ | — | — | — |
| Add/remove members, change roles (admin/member/viewer) | ✓ | ✓¹ | — | — |
| Grant/revoke `owner` role, transfer ownership | ✓ | — | — | — |
| Create / update / delete project, set retention | ✓ | ✓ | — | — |
| Create / revoke / rotate ingestion keys | ✓ | ✓ | — | — |
| View ingestion key metadata (never the secret after creation) | ✓ | ✓ | ✓ | — |
| Create / edit / enable / disable incident rules | ✓ | ✓ | — | — |
| Acknowledge / resolve incidents | ✓ | ✓ | ✓ | — |
| Start AI investigation | ✓ | ✓ | ✓ | — |
| Run synthetic generator against a project (needs a key) | ✓ | ✓ | — | — |
| View audit log (if implemented) | ✓ | ✓ | — | — |

¹ Admins cannot modify or remove owners or other admins' role to owner; the last owner cannot be removed or demoted.

## 6. Out of scope (MVP)

General-purpose chat / "ask anything" assistant · auto-remediation or any AI-triggered action · Kubernetes, Helm,
service mesh · microservices (beyond API + worker processes of one codebase) · paid services or paid AI tiers ·
distributed tracing UI, APM agents, OpenTelemetry collector ingestion (possible future) · email delivery,
SSO/OAuth, MFA (future) · alert notifications to Slack/PagerDuty (future) · billing · multi-region / HA ·
Elasticsearch/ClickHouse · mobile apps · unrelated products.

## 7. Phase plan (6–8 weeks)

Assumes ~20–25 focused hours/week. Weeks are targets; week 8 is buffer. Each phase ends with a PR, green CI and
an updated PROGRESS.md.

| Phase | Target | Scope | Acceptance criteria |
|---|---|---|---|
| **P0 Discovery** | Day 1 | Requirements, architecture, security, testing, decisions; two repos initialised. | Docs complete and consistent; environment facts recorded; versions verified with sources; no app code; branch `phase-0/discovery` pushed in both repos. |
| **P1 Scaffolding** | Week 1 | Server: uv project (Python 3.13), FastAPI app skeleton with layering, settings, structured logging, health endpoints, SQLAlchemy + Alembic baseline, Celery app + beat skeleton, Dockerfiles (non-root), `docker-compose.yml` (api, worker, beat, migrate, postgres:18, redis:8, ollama, web profile). Client: Next.js App Router + TS strict + Tailwind v4 + shadcn/ui, Vitest/RTL, Playwright config, OpenAPI type generation script. CI in both repos (least-privilege). | `docker compose up` healthy on the dev laptop with no port conflicts; `/api/v1/health/ready` green; CI green (lint, type-check, tests, audit, secret scan); lockfiles committed; versions pinned. |
| **P2 Auth + orgs + RBAC** | Week 2 | Users, sessions/refresh tokens, Argon2id, cookies, CSRF double-submit, login rate limit, orgs, memberships, RBAC dependencies, auth + org UI pages. | FR-01…07 done; tests: login/refresh/rotation/reuse-detection/logout, CSRF rejection, rate-limit, generic errors, RBAC matrix tests for every org endpoint, cross-org denial tests (404). |
| **P3 Projects + keys + ingestion + generator** | Week 3 | Projects CRUD, ingestion keys (show once, hash, revoke, rotate), ingest endpoint with limits/idempotency/rate limits, generator CLI, project settings UI. | FR-10…14; tests: key never returned twice, revoked key rejected, key cannot call user API, cookies cannot ingest, oversize/over-batch rejected (413/422), idempotent replay, cross-project isolation; generator can push ≥ 100k events locally. |
| **P4 Logs explorer + metrics** | Week 4 | Logs query API (filters, FTS, keyset pagination), metrics API (volume, error rate, p50/p95), overview dashboard + logs explorer UI. | FR-20…22; query plans use the documented indexes (EXPLAIN checked for main queries); bounded time windows enforced; NFR-04 target measured and recorded; UI accessible (axe check in Playwright). |
| **P5 Incident detection + lifecycle** | Week 5 | Rules, beat detection task, fingerprint dedup, incident list/detail/timeline, ack/resolve UI. | FR-30…33; deterministic tests using generator scenarios (spike → exactly one incident; continued spike → no duplicate; after resolve, new spike → new incident); partial unique index enforced; RBAC on transitions. |
| **P6 AI provider + Ollama investigation** | Week 6 | Provider Protocol, fake provider, Ollama provider (structured outputs via `format` JSON schema), context builder + redaction, Celery task, evidence validation, investigation UI. | FR-40…46; contract tests pass against fake provider; **AI acceptance suite** (≥ 3 generator scenarios) passes on local Ollama: valid schema output, all displayed evidence ids valid, timeout/failure paths produce `failed` gracefully, no state change to incidents; model id/duration/tokens recorded. |
| **P7 Hardening, E2E, docs** | Week 7 | Retention job, project deletion job, security review against SECURITY.md, Playwright E2E happy paths + authz negative paths, performance measurement, README/demo script/screenshots. | FR-50…53; E2E suite green in CI; dependency + secret scans clean or triaged; SECURITY.md residual risks updated; fresh-clone setup ≤ 15 min following README. |
| Buffer | Week 8 | Slack for overruns, polish, demo video. | — |
| **P8 Optional Gemini provider** | After P6 acceptance (week 8 or later) | Gemini free-tier provider behind the same Protocol; opt-in via `AI_PROVIDER=gemini`; synthetic/redacted data only. | Same AI acceptance suite passes with Gemini; Ollama remains default; no paid tier enabled; data-use notice documented (free-tier content may be used by Google to improve products — see DECISIONS ADR-010). |

### Cut list if behind schedule (in order)
1. P8 Gemini provider. 2. Audit log. 3. `error_count` rule type (keep error_rate + latency). 4. Key rotation overlap
(rotate = create + immediate revoke). 5. Project deletion UI (keep API). Security controls, tenant isolation tests
and the AI evidence validation are **never** cut.

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| CPU-only inference is slow (tens of seconds to minutes) | Poor demo UX | Small model, bounded context, async job + progress UI, timeout + clear failure, pre-pull model. |
| Small models produce weak/hallucinated analysis | Misleading output | Schema-constrained output, evidence validation, "limitations" field, UI labels output as AI-generated hypotheses. |
| Cross-repo contract drift | Broken client | OpenAPI as contract, generated types, CI drift check (see ADR-001/ADR-012). |
| Postgres log table growth | Slow queries, disk | Retention job, indexes, bounded windows; partitioning documented as future work. |
| Scope creep | Missed timeline | Out-of-scope list + cut list; phase acceptance gates. |
| Python 3.14 ecosystem gaps | Build failures | Use Python 3.13 (ADR-003). |
