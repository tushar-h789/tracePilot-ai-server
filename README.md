# TracePilot AI — Server

AI-assisted application observability and incident investigation platform (portfolio MVP).
This repository contains the **FastAPI API, Celery worker/beat, Alembic migrations, the Docker Compose stack for
local development, and (later) the synthetic log generator / Python SDK**. The web app lives in
[tracePilot-ai-client](https://github.com/tushar-h789/tracePilot-ai-client).

> Status: **Phase 0 — discovery & architecture (docs only).** No application code yet.

## Documentation (canonical for the whole system)
- [Project plan](docs/PROJECT_PLAN.md) — requirements, roles, phases, acceptance criteria
- [Architecture](docs/ARCHITECTURE.md) — C4 diagrams, modules, API, data model, AI provider contract, versions
- [Security](docs/SECURITY.md) — threat model (STRIDE), controls, AI risks, residual risks
- [Testing](docs/TESTING.md) — test pyramid, security tests, CI matrix, coverage
- [Decisions](docs/DECISIONS.md) — ADRs
- [Progress](docs/PROGRESS.md) — phase status and next steps

## Planned stack
FastAPI · Pydantic v2 · SQLAlchemy 2.x · Alembic · PostgreSQL 18 · Redis 8 · Celery 5.6 · Ollama (local LLM) ·
Python 3.13 (uv) · Docker Compose · Pytest + HTTPX · GitHub Actions.

## Local layout
```
tracePilot-ai/
  tracePilot-ai-server/   # this repo (compose builds the client from ../tracePilot-ai-client)
  tracePilot-ai-client/   # Next.js web app
```
Setup instructions arrive in Phase 1.
