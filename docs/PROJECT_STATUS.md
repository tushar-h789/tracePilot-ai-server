# Project status

_Last updated: 2026-10-17 17:05 (UTC+6)_

| Item | State |
| --- | --- |
| Current phase | **Phase 5: metrics aggregation and incident engine** (in progress) |
| Accepted phases | 0, 1, 2, 3, 4 (see [PHASE_ACCEPTANCE.md](PHASE_ACCEPTANCE.md)) |
| Branch policy | Commit directly to `main` (the user's instruction), Conventional Commits, never force-push |
| Server `main` | see `git log -1`; Phase 4 ended at `3432cf7` |
| Client `main` | Phase 4 ended at `9de1af6` |
| Running stack | compose project `tracepilot` (api/worker/beat/postgres/redis); web is the native Node server on 127.0.0.1:3100 |

## Phase 5 checklist
- [ ] Migration `0005`: incident rules, incidents (partial unique open fingerprint), incident events, detection runs
- [ ] Rule types: error rate, latency p95 (min samples), repeated error fingerprint, sustained error rate
- [ ] Detection engine (SQL windows, upsert by fingerprint, condition cleared/returned, no reopen on replayed data)
- [ ] Celery: beat schedule, evaluate task with bounded retries, time limits, run tracking, stale-run recovery
- [ ] API: rules CRUD + defaults, incidents list/detail/events/ack/resolve, detection runs trigger/list, org open incidents
- [ ] Tests: unit, integration, concurrency, worker failure/recovery; authz sweep covers new routes
- [ ] Bruno: `incident-rules/`, `incidents/`, `detection/` folders
- [ ] Client: incidents list/detail, rules page, dashboard incident cards, Vitest, Playwright
- [ ] Docs: ADRs, METRICS (what "requests" means), ARCHITECTURE, TESTING, PROGRESS, acceptance
- [ ] ci-local green, Actions green on both repos, stack updated (api/worker/beat, web on 3100)
