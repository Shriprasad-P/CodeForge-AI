# ADR 0001: AgentDock Core Architecture

**Status:** Accepted  
**Date:** 2026-08-06  
**Phase:** 1 foundation

## Context

AgentDock is a secure cloud coding-agent platform. The AI agent must plan and call tools, but all code execution must happen in an isolated sandbox. Secrets (GitHub App keys, LLM keys, DB credentials) must never enter the sandbox.

## Decision

### Service boundaries

| Service | Responsibility | Trust level |
|---------|----------------|-------------|
| `apps/web` | UI, WebSocket client | Untrusted browser |
| `apps/api` | Auth, sessions, GitHub App, orchestration, WS fan-out | Trusted |
| `apps/agent` | LLM planning + tool selection (no host Docker socket) | Trusted, no secrets to sandbox |
| `apps/worker` | Long-running jobs, retries, cancellation | Trusted |
| Sandbox (Docker) | Clone, edit, test, shell | Untrusted; short-lived GitHub token only |

### Data plane

- **PostgreSQL** — durable entities (users, installations, sessions, messages, tool calls, audit logs, PRs).
- **Redis** — locks, ephemeral session state, pub/sub for real-time events, job queues.

### Sandbox strategy

- Abstract `SandboxProvider` interface now.
- Implement **Docker** provider for local MVP.
- Later adapters: E2B, Daytona, Firecracker, Kubernetes — same interface.

### GitHub auth

- GitHub App (not PATs).
- Short-lived installation tokens; repository-scoped when starting a sandbox.
- Token injected only into the sandbox env; stripped on destroy.

### Agent model

- State machine (LangGraph or equivalent) outside the sandbox.
- Structured tools only; dangerous actions require approval modes (Autonomous / Balanced / Strict).
- Never push to default branch; never merge PRs.

### Observability

- Structured JSON logs with `request_id`, `session_id`, `sandbox_id`, `tool_call_id`.
- Prometheus `/api/metrics`; OpenTelemetry-compatible log fields.

## Consequences

- Clear security boundary: agent cannot reach DB/LLM secrets via sandbox tools.
- Extra hop (agent → API/worker → sandbox) adds latency; accepted for isolation.
- Docker-only MVP is not multi-tenant hard isolation; production will need Firecracker/K8s or a managed sandbox.

## Non-goals (Phase 1)

Auth, GitHub App, agent loop, sandbox provider, WebSocket workspace UI — deferred to later phases.