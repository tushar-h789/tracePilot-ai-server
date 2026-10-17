# Recovery guide (how a fresh agent resumes)

## 1. Establish state (read-only)
1. Machine `1c69639b-aa6d-4e49-bd1c-a445468c7502`; root `/home/tushar/Desktop/office/practice/tracePilot-ai`.
2. **`/home/tushar` is itself an unrelated git repo.** Before any git command, check that
   `git rev-parse --show-toplevel` is the server or client folder. Never run git in `/home/tushar`.
3. `git status -sb` and `git log --oneline -15` in both repos. Uncommitted work is the user's or a previous
   agent's. Never reset, checkout over it or discard it; commit it if it is clearly phase work and passes checks.
4. Read [PROJECT_STATUS.md](PROJECT_STATUS.md) (checklist), the end of [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md),
   and §4 below.
5. Stack: `docker compose ps` (server folder) and `ss -ltnp | grep 3100` (web).

## 2. Rules that always apply
- Push directly to `main`. Never force-push and never rewrite history. Conventional Commits.
- Never commit `.env` or secrets. Never print `.demo-credentials.local`. Build fake keys from pieces.
- **Memory:** before every heavy step (pytest, vitest, Playwright, builds, Bruno, docker build), check `free -m`.
  - Run each heavy step alone in `systemd-run --user --scope -q -p MemoryMax=<21900 − used − 100>M -p MemorySwapMax=0`.
  - If the headroom is too small, wait and retry a few times, then report the step NOT RUN.
  - Docker builds cannot be capped: only when headroom clearly allows (cached builds are cheap).
- Never drop `pgdata`, never touch user orgs. Demo data lives only in "TracePilot Demo (e2e)".
- Do not stop the user's processes (host Postgres 5432, ports 3000/3001).

## 3. Bounded-retry policy
| Situation | Policy |
| --- | --- |
| Flaky/failed test | Fix the cause. Rerun at most 3 times. If still failing, record it in DEVELOPMENT_LOG and mark the criterion FAIL, not PASS. |
| Memory headroom too small | Wait up to ~5 min (re-check every 10 s), at most 3 rounds. Then mark the step NOT RUN with the numbers. |
| OOM kill inside a scope | Lower the tool's footprint (single worker, smaller heap, split the build). At most 3 attempts. |
| GitHub Actions failure | Read the job logs, fix in a follow-up commit, push. At most 3 fix rounds before reporting BLOCKED. |
| Network/registry/API outage | Retry 3 times with backoff, then report BLOCKED. |
| Anything paid, destructive or data-dropping | Do not retry. Report BLOCKED and ask. |

## 4. Current checkpoint
- **Phase:** 5 (metrics aggregation and incident engine).
- **Last completed step:** Phase 4 pushed and accepted (server `3432cf7`, client `9de1af6`).
- **Next step:** implement migration 0005 and the detection engine (see the PROJECT_STATUS checklist).
- **How to resume:** finish the unchecked items in PROJECT_STATUS in order. Run `scripts/ci-local.sh`
  (`SKIP_DOCKER=1` plus a separate gitleaks run) before each push. Update this section at every checkpoint.

## 5. Useful commands
```bash
# server
set -a; eval "$(grep -E '^TEST_(DATABASE|REDIS)_URL=' .env)"; set +a; export TP_REQUIRE_INTEGRATION=1
SKIP_DOCKER=1 ./scripts/ci-local.sh
docker run --rm -m 300m -v "$PWD:/repo" ghcr.io/gitleaks/gitleaks:v8.30.1 git /repo --redact --no-banner
cd bruno && bun x @usebruno/cli@4.2.1 run --env Local
docker compose build api && docker compose run --rm migrate && docker compose up -d --no-deps --force-recreate api worker beat
# client (low-memory build)
NEXT_BUILD_CPUS=1 NEXT_SKIP_TYPECHECK=1 bunx next build --experimental-build-mode=compile
bunx next build --experimental-build-mode=generate && bun run postbuild
API_BASE_URL=http://127.0.0.1:8000/api/v1 NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=127.0.0.1 PORT=3100 \
  setsid nohup systemd-run --user --scope -q -p MemoryMax=1G node .next/standalone/server.js > /tmp/tracepilot/web.log 2>&1 &
```
