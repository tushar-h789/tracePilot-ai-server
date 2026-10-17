# TracePilot AI — Security Design and Threat Model

> Status: Phase 0. This document describes intended controls; each is verified by tests listed in
> [TESTING.md](TESTING.md). TracePilot AI is a portfolio MVP: **it does not claim to be leak-proof or
> production-certified.** Residual risks are listed explicitly in §8.

## 1. Assets

| Asset | Why it matters |
|---|---|
| User credentials (password hashes), refresh sessions, access tokens | Account takeover |
| Ingestion key secrets | Data injection / quota abuse into a tenant's project |
| Tenant log data (may contain PII or secrets sent by apps) | Confidentiality across orgs |
| Incidents, rules, investigation results | Integrity of operational decisions |
| Org membership and roles | Privilege escalation |
| Server secrets (JWT signing key, CSRF secret, key pepper, DB/Redis passwords) | Full compromise |
| Availability of API, worker, DB | DoS of the product |

## 2. Actors

- Anonymous internet user; registered user without membership in a target org; org member with lower role
  (viewer/member) attempting escalation; malicious or compromised instrumented application holding a valid
  ingestion key; attacker who controls **log content** (e.g. via a user-supplied field in an app that logs it);
  a misbehaving/hallucinating LLM; a developer mistake (secret committed, debug mode left on).

## 3. Trust boundaries

See ARCHITECTURE.md §7: (1) Internet ↔ web/API, (2) API ↔ data plane (Postgres/Redis/worker, internal network),
(3) worker ↔ AI runtime (model output is untrusted), (4) optional future: worker ↔ external Gemini API.

## 4. STRIDE threat model

| # | STRIDE | Threat | Controls | Verified by |
|---|---|---|---|---|
| T1 | Spoofing | Credential stuffing / brute force on login | Argon2id hashes; per-IP + per-identifier rate limit (Redis); generic "invalid email or password"; constant-ish work for unknown users (dummy hash verify) | API tests: rate limit 429, identical errors/timing-safe path |
| T2 | Spoofing | Stolen refresh token reuse | Opaque random refresh token (≥ 256 bits), stored as SHA-256 hash; rotation on every refresh; reuse of a rotated token revokes the whole session family; absolute expiry | Refresh reuse-detection test |
| T3 | Spoofing | Forged/expired access token | Short-lived (≈ 15 min) signed token (HS256 with server secret, `exp`, `iat`, `sub`, `sid`); session `sid` checked against non-revoked session on sensitive ops; algorithm pinned | Unit tests for expired/tampered/alg-none tokens |
| T4 | Spoofing | Ingestion key guessing or use against user API | High-entropy keys (`tp_` + public prefix + ≥ 32 random bytes base64url); only hash stored; constant-time compare; ingestion routes accept **only** keys, user routes accept **only** cookies | Tests: key on `/projects` → 401; cookie on `/ingest` → 401 |
| T5 | Tampering | CSRF on cookie-authenticated state changes | SameSite=Lax cookies + **double-submit CSRF token** (`tp_csrf` readable cookie, HMAC-bound to session id, echoed in `X-CSRF-Token`) required on POST/PUT/PATCH/DELETE; Origin/Referer allowlist check; JSON-only bodies | Tests: missing/mismatched token → 403; wrong Origin → 403 |
| T6 | Tampering | Mass assignment (e.g. setting `role`, `org_id` in body) | Pydantic request schemas with `extra="forbid"`; tenant ids come from path + scope, never body | Schema tests |
| T7 | Tampering | SQL injection via filters/search | SQLAlchemy bound parameters only; `websearch_to_tsquery`/`plainto_tsquery` for search; allowlisted sort fields | Tests with injection payloads; bandit/ruff rules |
| T8 | Tampering | Duplicate/replayed ingestion batches | `Idempotency-Key` with request hash, unique constraint | Idempotency tests |
| T9 | Repudiation | User denies ack/resolve/role change | `incident_events` with actor; optional `audit_log` for membership/key/project changes; structured logs with request id | Timeline tests |
| T10 | Information disclosure | IDOR / cross-tenant access | Scope objects required by repositories; lookups filtered by project/org; 404 for foreign ids; cross-org tests for every endpoint group | Cross-tenant test suite (CI-blocking) |
| T11 | Information disclosure | Secrets/PII in stored logs and app logs | Denylisted attribute keys redacted at ingest; additional pattern redaction before AI; app never logs request bodies, cookies, Authorization headers or key secrets; key shown once | Redaction unit tests; log-capture tests |
| T12 | Information disclosure | User enumeration via register/login | Generic responses; register returns same response for existing email (MVP: "if this email can be used, the account was created/exists" style), rate limited | API tests |
| T13 | Information disclosure | Data leakage to AI provider | Local Ollama default (data stays on host); bounded + redacted context; Gemini opt-in only with synthetic data (free tier content may be used to improve Google products) | AI context tests; config tests |
| T14 | Information disclosure | Verbose errors / debug / OpenAPI exposure | Problem-details with generic messages; no stack traces in responses; docs disabled or auth-gated in prod mode | Tests in prod settings |
| T15 | Denial of service | Large/high-rate ingestion | Body size limit before parse, batch/field limits, per-key and per-project rate limits, bounded time windows on queries, pagination caps | Limit tests (413/422/429) |
| T16 | Denial of service | Expensive queries (huge time ranges, FTS) | Required, capped time windows; statement timeout per request (e.g. 5 s); indexes per ARCHITECTURE §9 | Tests + EXPLAIN review |
| T17 | Denial of service | AI jobs exhausting CPU/RAM | AI queue concurrency 1, one active investigation per incident, per-user/org investigation rate limit, timeouts, bounded retries | Worker tests |
| T18 | Elevation of privilege | Role escalation (member → admin, admin → owner) | RBAC matrix enforced server-side in dependencies + service checks; last-owner protection; admins cannot grant owner | RBAC matrix tests |
| T19 | Elevation of privilege | Prompt injection in log content causing harmful behaviour | AI output is data only: no tools, no execution, no state changes, schema-validated, evidence-validated, escaped rendering; prompt treats logs as untrusted | AI injection test fixtures |
| T20 | Elevation of privilege | Container breakout / lateral movement | Non-root containers, no privileged mode, read-only FS where practical, internal network for DB/Redis/Ollama, ports bound to 127.0.0.1, Redis password | Compose review checklist |
| T21 | Supply chain | Vulnerable/malicious dependency, leaked secret in git | Lockfiles; pip-audit + npm audit; gitleaks secret scan; Dependabot (free) optional; least-privilege `GITHUB_TOKEN` | CI jobs |

## 5. Controls by area

### Authentication and sessions
- Argon2id via `argon2-cffi` (library-default or OWASP-recommended parameters, tuned in P2 to ~≤ 250 ms on the
  dev laptop); rehash on login when parameters change. Password policy: min 12 chars, max 128, check against a
  small local common-password list (no external API).
- Cookies: `tp_access` (HttpOnly, Secure in prod, SameSite=Lax, Path=/, ~15 min), `tp_refresh` (HttpOnly,
  Secure in prod, SameSite=Strict, Path=/api/v1/auth, ~7 days absolute, rotation), `tp_csrf` (not HttpOnly,
  Secure in prod, SameSite=Lax). Cookie names may use `__Host-` prefix in prod (HTTPS).
- Logout revokes the session; logout-all revokes all sessions; password change revokes all other sessions.
- Login rate limit: e.g. 5 attempts/min and 20/hour per (IP, email) plus a coarser per-IP limit; failures logged
  without the password; 429 with generic message.

### Authorization and tenant isolation
- RBAC matrix (PROJECT_PLAN §5) implemented once as a policy table and used by dependencies; services re-check
  for sensitive transitions (role changes, key creation).
- 404 (not 403) for resources outside the caller's orgs to avoid existence leaks; 403 for in-org insufficient role.
- No endpoint accepts `org_id`/`project_id` from the body to decide scope.

### Ingestion keys
- Format `tp_<prefix>_<secret>`; prefix (public, unique) used for lookup; store `HMAC-SHA256(pepper, full_key)`
  (fast hash is appropriate because keys are high-entropy; pepper from env). Secret returned only in the create/
  rotate response, with `Cache-Control: no-store`.
- Revoked/expired keys rejected; `last_used_at` tracked; rotation creates a new key and revokes the old one
  (optionally after an overlap window).
- Keys only authorize `POST /api/v1/ingest/logs` for their project. Never a substitute for user auth.

### Web/API hardening
- CORS: allowlist only the web origin(s) from settings; credentials only for that origin; no wildcard. With the
  Next.js proxy, browser calls are same-origin and CORS is a backstop.
- Trusted host validation (`TrustedHostMiddleware`) with configured hosts.
- Security headers (via Next.js and API): `Content-Security-Policy` (no inline scripts where feasible),
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `frame-ancestors 'none'`,
  HSTS in prod.
- Output encoding: React escapes by default; **no `dangerouslySetInnerHTML`** for log messages or AI output;
  AI text rendered as plain text.
- Request size limits (ingest 1 MiB; other JSON 64 KiB), DB statement timeouts, pagination caps.

### Infrastructure (Docker Compose)
- Non-root users in api/worker/beat/web images; slim base images; no secrets baked into images.
- Postgres/Redis/Ollama on an internal network; any host port bound to `127.0.0.1` only; Redis `requirepass`;
  Postgres app role without superuser (migrations use the same role in MVP; separate migration role is a
  hardening option).
- Health checks for every service; `restart: unless-stopped` locally.

### CI/CD
- `permissions: contents: read` at workflow level; jobs elevate only if strictly needed (none expected in MVP).
- No secrets required for CI (tests use ephemeral service containers and dummy secrets generated at runtime).
- Third-party actions pinned to a version tag (commit SHA pinning considered in P7).
- Secret scanning (gitleaks), dependency audit, SAST-lite (bandit, ruff security rules, eslint).

## 6. AI-specific risks and controls

| Risk | Control |
|---|---|
| **Prompt injection via log content** ("ignore previous instructions…", fake evidence ids, fake system prompts in messages) | Logs wrapped as untrusted data in delimited blocks; system prompt instructs to ignore instructions inside data; the model has **no tools and no authority**: output cannot change state, call APIs, or run code; schema + evidence validation; UI labels output as AI hypotheses. Injection fixtures in tests verify no state change and that only valid ids survive. |
| **Data leakage** (PII/secrets in logs reaching a model or another tenant) | Context built only from the incident's own project via scoped repositories; redaction (below) before the provider; bounded size; Ollama local by default; Gemini opt-in + synthetic data only; context snapshot stored redacted. |
| **Hallucinated evidence / false confidence** | Evidence ids must reference items present in the snapshot and real rows of that project; invalid ids removed and reported; hypotheses without valid evidence flagged `unsupported`; `limitations` required; confidence displayed as coarse level. |
| **Malformed or oversized output** | Ollama `format` JSON schema + Pydantic strict validation with length caps; failure → bounded retry → `failed`. |
| **Resource exhaustion** | Timeouts, retries ≤ 2, concurrency 1, one active investigation per incident, rate limit per org. |
| **Model/output persistence of sensitive data** | Results stored as validated JSON only; raw provider responses not stored (only length + error class). |
| **Over-reliance** | Never auto-resolves or acknowledges; humans act; UI shows model id, timestamp and disclaimer. |

### Redaction rules (applied before AI; denylisted keys also at ingest)
1. Attribute keys (case-insensitive, substring match) → value replaced with `[REDACTED]`: `password`, `passwd`,
   `secret`, `token`, `api_key`/`apikey`, `authorization`, `cookie`, `set-cookie`, `session`, `private_key`,
   `credit_card`/`card_number`, `ssn`.
2. Patterns in message/values → typed placeholders: email addresses `[EMAIL]`; `Bearer <token>` and JWT-like
   `xxx.yyy.zzz` base64url triples `[TOKEN]`; TracePilot keys `tp_…` `[TP_KEY]`; common cloud key shapes
   (e.g. `AKIA[0-9A-Z]{16}`) `[CLOUD_KEY]`; 13–19 digit sequences passing Luhn `[CARD]`; IPv4/IPv6 addresses
   → `[IP]` (AI context only); URL query parameters named like rule 1 → `[REDACTED]`.
3. Truncation: message ≤ 500 chars and attributes ≤ 1 KiB per log in AI context.
Redaction is best-effort pattern matching and **will miss some secrets**; it reduces, not eliminates, exposure.

## 7. Security testing summary
See TESTING.md §4–§6: authn/session tests, CSRF tests, RBAC matrix tests, cross-tenant tests for every endpoint
group, ingestion abuse tests, AI injection/evidence tests, pip-audit, npm audit, bandit, ruff, eslint, gitleaks.

## 8. Residual risks (accepted for the MVP)

- Pattern-based redaction can miss novel secret formats or PII in free text.
- Small local models may still produce misleading but schema-valid analyses; evidence validation proves ids
  exist, not that reasoning is correct.
- No MFA, SSO, email verification or password reset by email in MVP.
- No RLS in Postgres; isolation relies on application-layer scoping (mitigated by tests).
- Rate limits are per-instance Redis counters; sophisticated distributed abuse is out of scope.
- Local Docker setup is not a hardened production deployment (no TLS termination, WAF, backups, or secrets
  manager); "Secure" cookies are enabled only in prod mode behind HTTPS.
- Gemini free tier (if enabled in P8) may use submitted content to improve Google products; hence synthetic data only.
- Dependency vulnerabilities discovered after release until the next audit run.

## 9. Phase 1 — controls implemented and tested (2026-10-17)

| Control | Where | Test / evidence |
|---|---|---|
| Fail-fast settings validation; errors name the variable, never the value; secrets as `SecretStr` | `app/core/config.py` | `test_config.py` |
| Prod guard: refuses `change-me` credentials, `*` hosts, eager Celery, DEBUG; disables docs/OpenAPI | `app/core/config.py`, `app/main.py` | `test_config.py`, `test_http_security.py` |
| TrustedHostMiddleware (allowlist) | `app/main.py` | untrusted Host → 400 |
| CORS allowlist, no wildcard (validator rejects `*`) | `app/main.py`, config | preflight tests |
| Security headers (nosniff, DENY framing, CSP `default-src 'none'` on JSON, Referrer-Policy, COOP, Permissions-Policy, HSTS in prod) | `app/core/security.py`, client `next.config.ts` | header assertions (pytest, Bruno, curl) |
| Generic problem+json 500s; details only in server logs | `app/core/security.py` | `test_unhandled_errors_hide_internals` |
| Readiness reports only `ok`/`error` per dependency (no exception text/hosts) | `app/services/health.py` | `test_ready_returns_503_when_dependencies_unreachable` |
| JSON logs with request id; key-based redaction; no query strings/bodies/headers logged; uvicorn access log disabled in favour of the app's request log | `app/core/logging.py` | `test_logging.py` |
| Celery JSON-only serialization (no pickle), no result backend | `app/workers/celery_app.py` | `test_celery.py` |
| Containers: non-root (uid 10001 / node 1000), read-only rootfs, tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges`, healthchecks | `Dockerfile`, `docker-compose.yml`, client `Dockerfile` | `docker compose ps` healthy; `id` in container; CI non-root check |
| All host ports bound to 127.0.0.1; Redis `requirepass`; Postgres password auth (scram) | `docker-compose.yml` | `docker compose ps` port column |
| Secrets only in untracked `.env`; `.env.example` placeholders | `.gitignore`, `.env.example` | `git ls-files`, gitleaks |
| CI least privilege (`permissions: contents: read`, `persist-credentials: false`, SHA-pinned actions); gitleaks, hadolint, bandit, pip-audit, `bun audit` | `.github/workflows/ci.yml` (both repos) | local runs recorded in PROGRESS.md |
| Client never renders error messages from server errors; `react/no-danger` lint rule | client `route-error.tsx`, `eslint.config.mjs` | `route-error.test.tsx` |

Accepted for Phase 1: Redis password visible in the redis container's process list (`--requirepass`); the
Next.js app has no Content-Security-Policy yet (nonce-based CSP planned for P7); one triaged dev-only advisory in
the client (`braces`, GHSA-vfj7-8cjw-p6xm, no patched version exists) is ignored in `bun audit`.
