# Development history

AgentDock was delivered in a sequence of reliability and security checkpoints. This page keeps the historical implementation map available without making it the product story.

| Stage | Focus |
| --- | --- |
| 1 | Monorepo, Compose, health endpoints, and the initial web shell |
| 2 | Email/password authentication, sessions, and ownership foundations |
| 3 | GitHub App linking, installations, repository connections, and webhooks |
| 4 | Secure sandbox runtime, queue delivery, constrained commands, and cancellation |
| 5 | Bounded coding-agent state machine, tools, validation, and immutable publication foundations |
| 6 | Durable outbox/recovery, realtime workspace, observability, and deterministic full-system E2E |
| 7 | Product positioning, workflow UX, review clarity, documentation, and portfolio readiness |

The current product deliberately preserves the control-plane trust boundaries established by the earlier stages. See the source-backed documents in `docs/` for the behavior and guarantees that matter at runtime.
