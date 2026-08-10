# AgentDock API Specification

Base URL: `http://localhost:8000`

## Health (Phase 1)

### `GET /api/health`

Liveness probe. Does not check Postgres or Redis.

### `GET /api/ready`

Readiness probe. Returns **503** if Postgres or Redis is unreachable.

### `GET /api/metrics`

Prometheus text exposition (`agentdock_*` gauges).

## Auth (Phase 2)

Cookie name: `agentdock_session` (HttpOnly). Send with `credentials: include` from the web app.

### `POST /api/auth/register` → **201**

```json
{ "email": "ada@example.com", "password": "password123", "display_name": "Ada" }
```

Returns `{ "user": { "id", "email", "display_name" } }` and sets the session cookie.
Duplicate email → **409**. Validation errors → **422**.

### `POST /api/auth/login` → **200**

```json
{ "email": "ada@example.com", "password": "password123" }
```

Invalid credentials → **401** with generic `"Invalid email or password"`.

### `POST /api/auth/logout` → **204**

Revokes the DB session and clears the cookie.

### `GET /api/auth/me` → **200** / **401**

Authenticated user payload (no password fields).

## Agent sessions (Phase 2 foundation)

### `POST /api/agent-sessions` → **201**

```json
{ "title": "Fix flaky test" }
```

Creates a row with `status: "created"` owned by the current user.

### `GET /api/agent-sessions/{id}` → **200** / **404**

Returns the session only when `user_id` matches the authenticated user.

## GitHub (Phase 3)

Requires an authenticated session. If GitHub App env vars are missing, status returns `configured: false` and mutating connect routes return **503**.

See [github-app.md](./github-app.md) for App setup.

### `GET /api/github/status` → **200**

`{ configured, linked, github_login, installation_count, connection_count }`

### `GET /api/github/connect` → **200** / **503**

Returns `{ authorize_url }` for GitHub OAuth (server-built; state bound to current user).

### `GET /api/github/callback` → **302**

OAuth callback. Exchanges code server-side, links GitHub identity to the existing AgentDock user, then redirects into App install.

### `GET /api/github/setup` → **302**

GitHub App setup URL. Claims an installation for the authenticated user via OAuth state.

### `GET /api/github/account` → **200**

Linked GitHub account metadata, or `null`.

### `GET /api/github/installations` → **200**

Installations owned by the current user only.

### `GET /api/github/repositories?installation_id=&page=&per_page=` → **200**

Repositories accessible through that installation (installation token generated server-side; never returned).

### `POST /api/github/repositories/{repository_id}/connect` → **201**

```json
{ "installation_id": "<uuid>", "github_repository_id": 123456789 }
```

Validates accessibility via GitHub, then persists a `repository_connections` row.

### `GET /api/github/connections` → **200**

Active repository connections for the current user.

### `DELETE /api/github/connections/{connection_id}` → **204**

Disconnects locally. Does **not** uninstall the GitHub App.

### `POST /api/github/webhooks` → **200**

GitHub App webhooks. Requires valid `X-Hub-Signature-256`. Handles `installation` and `installation_repositories`. Duplicate `X-GitHub-Delivery` is idempotent.

## Executions (Phase 4)

Authenticated. Jobs are scoped to `current_user.id`. Commands are argv arrays (no shell).

### `POST /api/executions` → **201**

```json
{
  "repository_connection_id": "<uuid>",
  "command": ["python", "-m", "pytest", "-q"],
  "working_directory": null,
  "agent_session_id": null
}
```

Persists `queued` and enqueues to Redis. Does not wait for completion.

### `GET /api/executions` → **200**

### `GET /api/executions/{id}` → **200** / **404**

### `GET /api/executions/{id}/logs` → **200** / **404**

Bounded stdout/stderr (`output_truncated` when clipped).

### `POST /api/executions/{id}/cancel` → **200** / **409**

## Agent runs (Phase 5)

### `GET /api/agent-runs/status` → **200**

`{ configured, provider, model }`

### `POST /api/agent-runs` → **201** / **503**

```json
{ "repository_connection_id": "<uuid>", "task": "Fix empty username validation." }
```

### `GET /api/agent-runs` → **200**

### `GET /api/agent-runs/{id}` → **200** / **404**

### `GET /api/agent-runs/{id}/steps` → **200**

### `GET /api/agent-runs/{id}/diff` → **200**

### `POST /api/agent-runs/{id}/cancel` → **200** / **409**

### `POST /api/agent-runs/{id}/approve` → **200** / **404** / **409**

Owner-only approval of the persisted validated diff. Queues trusted publication.

### `POST /api/agent-runs/{id}/reject` → **200** / **404** / **409**

Owner-only terminal rejection while the run awaits approval.

## Realtime WebSocket (Phase 6)

### `WS /ws/agent-runs/{run_id}`

Authenticate with the same HttpOnly `agentdock_session` cookie. Ownership required.

On connect: `agent.snapshot` (compact). Then live versioned events (`version: 1`).

Cancel and mutations stay on REST. Diff payloads stay on `GET /api/agent-runs/{id}/diff`.

See [realtime.md](./realtime.md) for event schema, close codes, proxy notes.

## Later phases

Browser tools. See [publication.md](./publication.md) for Phase 7 commit/push/PR behavior.
