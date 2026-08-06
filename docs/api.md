# AgentDock API Specification (Phase 1)

Base URL: `http://localhost:8000`

## Health

### `GET /api/health`

Liveness probe. Does not check Postgres or Redis.

**200**
```json
{
  "status": "ok",
  "service": "AgentDock",
  "version": "0.1.0",
  "timestamp": "2026-08-06T10:00:00+00:00"
}
```

### `GET /api/ready`

Readiness probe. Returns **503** if Postgres or Redis is unreachable.

**200 / 503**
```json
{
  "status": "ready",
  "checks": { "postgres": true, "redis": true },
  "timestamp": "2026-08-06T10:00:00+00:00"
}
```

### `GET /api/metrics`

Prometheus text exposition.

```text
agentdock_up 1
agentdock_postgres_up 1
agentdock_redis_up 1
```

## Later phases

Auth, GitHub, sessions, WebSocket streaming — see the product brief and phase plan in the README.