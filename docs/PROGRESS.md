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
