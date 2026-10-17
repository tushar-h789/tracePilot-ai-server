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
bun x @usebruno/cli@4.2.1 run --env Local  # all requests, assertions and tests
bun x @usebruno/cli@4.2.1 run health --env Local --reporter-json /tmp/bruno.json
```

## Layout and conventions
```
bruno/
  opencollection.yml        # collection root (name, default environment)
  environments/Local.yml    # baseUrl=http://localhost:8000/api/v1 — no secrets, ever
  health/                   # one folder per API module
    folder.yml              # folder metadata + docs
    live.yml                # GET /health/live
    ready.yml               # GET /health/ready
```
- **One folder per backend module**, named like the router (`/api/v1/<module>`). Future folders are created
  only when the module's endpoints exist: `auth`, `organizations`, `projects`, `ingestion-keys`, `ingest`,
  `logs`, `metrics`, `incidents`, `investigations`. No empty placeholder folders.
- File names are kebab-case verbs/nouns (`create-project.yml`); `info.seq` orders requests inside a folder.
- Every request has **declarative assertions** (`runtime.assertions`, at least the status code) and **tests**
  (`runtime.scripts` type `tests`) for the response shape. Negative cases (401/403/404/422) live next to the
  happy path in the same folder.
- Requests use `{{baseUrl}}`; never hard-code hosts.
- No auth block on public endpoints (omitting `http.auth` = no auth). Authenticated modules (Phase 2) will set
  auth at folder level and requests use `auth: inherit`.

## Secrets
Environment files are committed and must contain **no secrets**. For sensitive values use either:
- Bruno **secret variables** (mark a variable as secret in the app — the value is stored locally, not in the file;
  YAML shows `secret: true`), or
- a `bruno/.env` file (gitignored; template `bruno/.env.example`) referenced as `{{process.env.NAME}}`.
