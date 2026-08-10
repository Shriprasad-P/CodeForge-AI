# Real-Time Agent Workspace Streaming (Phase 6)

AgentDock streams **visibility** events for coding-agent runs. Postgres remains authoritative; Redis Pub/Sub + WebSockets are for responsiveness only.

## Architecture

```text
Worker (state transitions + sandbox exec)
  → AgentEventPublisher
  → Redis Pub/Sub channel agentdock:run:{run_id}
  → FastAPI WebSocket /ws/agent-runs/{run_id}
  → Authenticated browser
```

REST remains the source of truth for run status, steps, diffs, and cancellation.

## Redis transport: Pub/Sub (not Streams)

**Choice:** Redis **Pub/Sub**.

**Why:** Phase 6 reconnect recovers from REST (`agent_runs` + `agent_steps`). Replayable Streams would add complexity without changing correctness. Sequence numbers still come from Redis `INCR` (`agentdock:runseq:{run_id}`) so clients can order/dedupe live events.

## WebSocket endpoint

```text
GET /ws/agent-runs/{run_id}
```

Same origin/port as the API (Compose: `localhost:8000`). Override with `NEXT_PUBLIC_WS_URL` if the browser cannot derive `ws://` / `wss://` from `NEXT_PUBLIC_API_URL`.

### Authentication and ownership

1. Resolve HttpOnly cookie `agentdock_session`
2. Resolve current user
3. Load `agent_run`
4. Require `agent_run.user_id == current_user.id`

Reject **before** accepting the socket when possible.

| Condition | Close code | Reason |
|-----------|------------|--------|
| Unauthenticated | 4401 | Not authenticated |
| Wrong owner | 4403 | Forbidden |
| Missing run | 4404 | Run not found |
| Connection limit | 4429 | Connection limit |
| Server error | 1011 | Server error |

Limits (defaults): `WS_MAX_CONNECTIONS_PER_USER=10`, `WS_MAX_CONNECTIONS_PER_RUN=5`.

WebSocket messages from the browser cannot invoke tools. Cancel remains `POST /api/agent-runs/{id}/cancel`.

## Event protocol (version 1)

```json
{
  "version": 1,
  "event": "agent.tool.completed",
  "run_id": "…",
  "sequence": 17,
  "timestamp": "2026-08-10T12:00:00+00:00",
  "data": {}
}
```

Clients must ignore unsupported `version` values without crashing.

### Event types

| Event | Notes |
|-------|-------|
| `agent.snapshot` | Sent once on connect (local; not fan-out). Compact status + changed_files. |
| `agent.run.queued` / `started` / `status` | Lifecycle |
| `agent.step.started` / `completed` | Loop progress |
| `agent.tool.started` / `completed` | Safe operational summaries only |
| `agent.command.output` | Ephemeral stdout/stderr chunks |
| `agent.validation.started` / `completed` | Validation |
| `agent.files.changed` | Filename list |
| `agent.diff.ready` | Notification — fetch diff via REST |
| `agent.run.completed` / `failed` / `cancelled` / `timed_out` / `step_limit_reached` | Terminal |
| `agent.ping` / `agent.pong` | Heartbeat (~25s) |

No chain-of-thought. No secrets. No full file contents in tool events.

## Durable vs ephemeral

| Durable (Postgres) | Ephemeral (stream only) |
|--------------------|-------------------------|
| Run status, steps, validation, changed files, diff metadata, errors | Partial command stdout/stderr chunks |

Ephemeral chunks may be dropped under backpressure; final bounded tool summaries remain in `agent_steps`.

## Sequencing

Monotonic `sequence` per run via Redis `INCR`. Safe for single-run orchestration. Do not rely only on WebSocket arrival order.

## Reconnection

Frontend:

1. Connect WebSocket
2. Apply events (dedupe by sequence)
3. On disconnect: exponential backoff (0.5s → 8s)
4. Refetch run/steps/diff over REST
5. Resume live updates

Correctness does not require continuous connectivity.

## Backpressure

- Worker never waits on browsers (Redis decouples)
- Per-socket outbound queue capped (256)
- Ephemeral `agent.command.output` dropped first when full
- Durable events may displace oldest queue items

## Command output streaming

Sandbox `exec` demuxes Docker streams and calls `on_chunk(stream, text, truncated)` incrementally. Limits from Phase 4 (`sandbox_max_output_bytes`) still apply. Chunks are UTF-8 with replacement and ~512-char slices for the wire.

## Frontend

`/agent` live workspace:

- Status, activity timeline, command output panel, changed files, diff (REST on `agent.diff.ready`), Cancel (REST)
- Auto-scroll logs only while near bottom
- Plain-text rendering (no `dangerouslySetInnerHTML`) — repository output is untrusted
- Polling fallback when WebSocket is down

## Reverse proxy (production)

Proxies must forward:

- `Upgrade: websocket`
- `Connection: upgrade`
- Idle/read timeouts long enough for heartbeats (~25s) plus agent runtime
- Prefer `wss://` behind TLS terminators

Without correct Upgrade handling, clients fall back to REST polling.

## Redis outage

| Failure | Behavior |
|---------|----------|
| Event publish fails | Agent continues; run state still persisted; UI may miss live events |
| Queue unavailable | Existing Phase 4/5 behavior (cannot claim new work) |

Distinguish queue failure from event-publish failure.

## Metrics / logs

Metrics: `websocket_active_connections`, `agent_events_published_total`, `agent_event_publish_failures_total`, `websocket_disconnects_total`.

Logs: `websocket.connected`, `websocket.disconnected`, `websocket.unauthorized`, `agent.event.published`, `agent.event.publish_failed`.

## Database

**No migration required for Phase 6.** Existing `agent_runs` + `agent_steps` are sufficient.

## Protocol evolution

Bump `version` for breaking changes. Keep v1 readers ignoring unknown event names when safe.
