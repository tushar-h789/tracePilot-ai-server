# Development log

Chronological, factual notes (Asia/Dhaka, UTC+6). Newest entries at the bottom.

## 2026-10-17

- **Phases 0–4** delivered and accepted; details in [PROGRESS.md](PROGRESS.md).
- **Phase 4 incidents worth remembering:**
  - a plain `next build` was OOM-killed under the ~0.9 GB memory cap, so the client is built in two steps
    (`--experimental-build-mode=compile`, then `generate`);
  - port 3100 was briefly down while `.next/standalone` was missing.
- **17:00** Phase 5 started. Added PROJECT_STATUS, DEVELOPMENT_LOG, RECOVERY and PHASE_ACCEPTANCE.
