# TracePilot AI — Bruno collection

API collection for [Bruno](https://www.usebruno.com/) in the **OpenCollection YAML** format
(`opencollection.yml` at the root, one `.yml` file per request). This is the default format for new collections
since Bruno 3.1 and opens natively in Bruno 4.x (verified with the desktop app 4.2.1 and `@usebruno/cli` 4.2.1).

## Open in the desktop app
Bruno → **+** → **Open Collection** → select this `bruno/` folder
(`<repo>/tracePilot-ai-server/bruno`). Choose the **Local** environment (top right; it is also the collection
default).

## Run headlessly (CLI)
```bash
docker compose up -d                       # API on http://localhost:8000
cd bruno
bunx @usebruno/cli run --env Local         # all requests, assertions and tests (run from bruno/)
bun x @usebruno/cli@4.2.1 run health --env Local --reporter-json /tmp/bruno.json
```

## Layout and conventions
```
bruno/
  opencollection.yml        # collection root (name, default environment)
  environments/Local.yml    # baseUrl=http://localhost:8000/api/v1 — no secrets, ever
  health/                   # one folder per API module (seq 1)
    folder.yml              # folder metadata + docs
    live.yml                # GET /health/live
    ready.yml               # GET /health/ready
  auth/                     # seq 2: csrf, register owner/member/outsider, login, me, sessions, logout, 401 after logout
  organizations/            # seq 3: list, create (sets orgId), get, list after create
  organization-members/     # seq 4: list, add (sets memberUserId), change role, demote last owner (409), remove, audit logs
  negative-cases/           # seq 5: bad login 401, missing CSRF 403, cross-tenant get/add 404 as an outsider
```

### How authentication works in the collection (Phase 2)
- Bruno's cookie jar keeps `tp_session` / `tp_csrf` between requests (desktop app and CLI).
- `auth/csrf` creates a per-run `runId`, so emails (`bruno-owner-{{runId}}@{{emailDomain}}`) are unique and the
  collection can be re-run against the same database.
- After-response scripts copy the `tp_csrf` value from `Set-Cookie` into the runtime variable `{{csrfToken}}`;
  unsafe requests send `X-CSRF-Token: {{csrfToken}}`.
- The run is ordered (folders by `seq`): register three users, act as the owner, then sign in as the outsider for
  the negative cases. Run the whole collection, not a single folder, unless the variables already exist.
- `demoPassword` in `environments/Local.yml` is a throwaway local-only passphrase for synthetic users, not a secret.
- **One folder per backend module**, named like the router (`/api/v1/<module>`). Future folders are created
  only when the module's endpoints exist: `auth`, `organizations`, `projects`, `ingestion-keys`, `ingest`,
  `logs`, `metrics`, `incidents`, `investigations`. No empty placeholder folders.
- File names are kebab-case verbs/nouns (`create-project.yml`); `info.seq` orders requests inside a folder.
- Every request has **declarative assertions** (`runtime.assertions`, at least the status code) and **tests**
  (`runtime.scripts` type `tests`) for the response shape. Negative cases (401/403/404/422) live next to the
  happy path in the same folder.
- Requests use `{{baseUrl}}`; never hard-code hosts.
- No `http.auth` blocks: authentication is the session cookie held by the cookie jar (see above).

## Secrets
Environment files are committed and must contain **no secrets**. For sensitive values use either:
- Bruno **secret variables** (mark a variable as secret in the app — the value is stored locally, not in the file;
  YAML shows `secret: true`), or
- a `bruno/.env` file (gitignored; template `bruno/.env.example`) referenced as `{{process.env.NAME}}`.
