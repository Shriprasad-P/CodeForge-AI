# AgentDock Architecture

> Product name: **AgentDock**. GitHub repository: **CodeForge-AI**.
> Container names, metrics (`agentdock_*`), and DB identifiers use the product name.

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
| 2 | Email/password auth, Alembic models/migrations, auth + agent session foundations, dashboard |
| 3 | GitHub App install/link, repository discovery, repository connections, webhooks |
| 4 | Secure sandbox runtime + worker execution |
| 5 | Coding agent state machine + tools |
| 6 | WebSocket workspace UI |
| 7 | Diff, approval, trusted commit/push, PR |
| 8 | Playwright browser tools |
| 9 | Observability hardening + E2E |

## Phase 2 data model

- `users` — identity (email unique, Argon2id `password_hash`, `auth_provider` reserved for GitHub)
- `auth_sessions` — browser login sessions (`token_hash`, `expires_at`, `revoked_at`)
- `agent_sessions` — coding-task foundation (`user_id`, `title`, `status=created` only)

Auth cookie sessions are **not** the future coding-agent workspace sessions.

## Phase 3 data model

- `github_accounts` — GitHub identity linked to an AgentDock user (1:1)
- `github_installations` — App installations claimed by a user (unique `github_installation_id`)
- `repository_connections` — selected repos keyed by GitHub numeric repository ID
- `github_webhook_deliveries` — minimal delivery-id ledger for webhook idempotency

Installation access tokens are **not** stored. They are minted for GitHub API calls and discarded.

## Phase 4 data model

- `execution_jobs` — queued/running/terminal sandbox runs scoped to `user_id` + `repository_connection_id`

Queue: Redis list `agentdock:executions`. Worker is a privileged control-plane service (Docker socket). Sandboxes are ephemeral, non-root, no socket, network disabled by default. See [sandbox.md](./sandbox.md).

## Phase 5 data model

- `agent_runs` — bounded coding-agent runs
- `agent_steps` — operational tool/finish trace (no chain-of-thought)

LLM keys stay on the control plane. See [agent.md](./agent.md).

## Phase 6 realtime

- WebSocket `/ws/agent-runs/{run_id}` authenticated via session cookie + run ownership
- Redis Pub/Sub `agentdock:run:{run_id}` bridges workers ↔ API replicas
- Postgres remains authoritative; see [realtime.md](./realtime.md)

## Trust boundaries

See [security.md](./security.md) and [ADR 0001](./adr/0001-architecture.md).

## Packages

| Package | Role |
|---------|------|
| `shared-types` | Cross-service event/API types |
| `agent-tools` | Tool schemas shared by agent + API |
| `sandbox-sdk` | Provider interface + Docker impl |
| `github-client` | GitHub App JWT + installation tokens (API services also implement this in Phase 3) |
