# AgentDock Architecture

## Overview

AgentDock runs an AI coding agent against an isolated sandbox. The agent never executes inside the user’s repository container; it only issues structured tool calls that the worker/API forward to a sandbox provider.

```text
Browser (Next.js)
    │  HTTPS + WebSocket
    ▼
API (FastAPI) ──► PostgreSQL
    │         └──► Redis (events, locks, queues)
    ├──► Agent service (LLM + state machine)
    └──► Worker ──► SandboxProvider ──► Docker container
```

## Phases

| Phase | Scope |
|-------|-------|
| 1 | Monorepo, Compose, Postgres, Redis, API health, Next.js shell |
| 2 | Auth, models, sessions |
| 3 | GitHub App |
| 4 | Docker sandbox provider |
| 5 | Agent state machine + tools |
| 6 | WebSocket workspace UI |
| 7 | Diff, approval, PR |
| 8 | Playwright browser tools |
| 9 | Observability hardening + E2E |

## Trust boundaries

See [security.md](./security.md) and [ADR 0001](./adr/0001-architecture.md).

## Packages

| Package | Role |
|---------|------|
| `shared-types` | Cross-service event/API types |
| `agent-tools` | Tool schemas shared by agent + API |
| `sandbox-sdk` | Provider interface + Docker impl |
| `github-client` | GitHub App JWT + installation tokens |