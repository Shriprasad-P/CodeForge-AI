# AgentDock

> **Product name:** AgentDock · **GitHub repository:** [CodeForge-AI](https://github.com/Shriprasad-P/CodeForge-AI)

Secure cloud coding-agent platform. Users connect a GitHub App, pick a repository, describe a task, and an AI agent works inside an isolated sandbox — then opens a pull request after approval.

> Phase 6 status: Phase 1–5 preserved + authenticated WebSocket agent workspace streaming.

![AgentDock status panel](docs/assets/screenshot-placeholder.svg)

## Architecture

See [docs/architecture.md](docs/architecture.md) and [docs/adr/0001-architecture.md](docs/adr/0001-architecture.md).

```text
CodeForge-AI/                 # git repo (product: AgentDock)
├── apps/
│   ├── web/          # Next.js (App Router)
│   ├── api/          # FastAPI + Alembic
│   ├── agent/        # LLM agent (Phase 5)
│   └── worker/       # Job worker (Phase 4+)
├── packages/         # shared-types, agent-tools, sandbox-sdk, github-client
├── infrastructure/
├── tests/
├── docs/
├── docker-compose.yml
└── .env.example
```

Internal identifiers (`agentdock-*` containers, `agentdock_*` metrics, DB user/db name) use the product name **AgentDock**.

## Authentication (Phase 2)

- Email/password registration and login
- Passwords hashed with **Argon2id**
- Auth sessions stored in Postgres (`auth_sessions`); only a **SHA-256 hash** of the cookie token is persisted
- Browser session via **HttpOnly** cookie `agentdock_session` (`SameSite=Lax`; `Secure` auto-enabled outside development)
- Redis fixed-window rate limit on `/api/auth/login` and `/api/auth/register`
- Minimal `agent_sessions` table for ownership foundations (no agent execution yet)

## GitHub App (Phase 3)

- Link GitHub identity to an existing AgentDock user (does not create duplicate users)
- Install the App on personal accounts or organizations
- Discover repositories via **installation tokens** (temporary, server-side only)
- Persist selected `repository_connections` by GitHub repository ID
- Webhooks: `installation`, `installation_repositories` with signature verification
- Without GitHub env vars the stack still starts; UI shows integration not configured

Setup guide: [docs/github-app.md](docs/github-app.md).

## Sandbox executions (Phase 4)

- Create execution jobs for **owned** repository connections
- Worker claims jobs from a Redis queue and runs them in labelled ephemeral containers
- Repository materialization on the worker host (fixture mode by default; GitHub clone when configured)
- Argv-only commands inside the sandbox (no shell); bounded logs; cancel + timeout
- See [docs/sandbox.md](docs/sandbox.md)

## Coding agent (Phase 5)

- Bounded inspect → edit → validate loop via tools in the sandbox
- Providers: `fake` (CI/local) or `openai`
- Soft-disables when LLM is unset; platform still boots
- See [docs/agent.md](docs/agent.md)

## Real-time workspace (Phase 6)

- Authenticated WebSocket `/ws/agent-runs/{run_id}` (session cookie + ownership)
- Redis Pub/Sub bridge; Postgres remains authoritative
- Live status, tool activity, incremental command output, diff-ready notifications
- Reconnect + REST recovery; polling fallback
- See [docs/realtime.md](docs/realtime.md)

## Quick start (local)

### Prerequisites

- Docker Desktop / Docker Engine + Compose
- Python **3.12 or 3.13** (bare `python3` may be 3.14 — not supported yet)
- Node.js **22+**

### 1. Environment

```bash
cp .env.example .env
```

Postgres host port **5433**, Redis **6380** by default (avoids clashes with local 5432/6379).

GitHub App variables are optional for boot. Add them when testing connect/install (see [docs/github-app.md](docs/github-app.md)).

### 2. Infrastructure only

```bash
docker compose up -d postgres redis
```

### 3. API

```bash
cd apps/api
python3.13 -m venv .venv   # or python3.12
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Compose runs `alembic upgrade head` in the API entrypoint before uvicorn.

Verify:

```bash
curl -s http://localhost:8000/api/health | jq
curl -s http://localhost:8000/api/ready | jq
curl -s -c /tmp/ad.ck -X POST http://localhost:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","password":"password123","display_name":"Ada"}' | jq
curl -s -b /tmp/ad.ck http://localhost:8000/api/auth/me | jq
curl -s -b /tmp/ad.ck http://localhost:8000/api/github/status | jq
```

### 4. Web

```bash
cd apps/web
npm ci
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) → Register / Login → Dashboard → Manage GitHub.

### Full stack via Compose

```bash
cp .env.example .env
docker compose up --build
```

- Web: http://localhost:3000
- API docs: http://localhost:8000/docs

## Tests

```bash
# API (requires Postgres + Redis; Compose infra is fine)
cd apps/api && source .venv/bin/activate && alembic upgrade head && pytest -q

# Worker / sandbox (requires Docker daemon + sandbox image)
docker build -t agentdock-sandbox:local -f infrastructure/sandbox/Dockerfile infrastructure/sandbox
cd apps/worker && pip install -r ../api/requirements.txt -r requirements.txt -e ../../packages/sandbox-sdk && pytest -q

# Web
cd apps/web && npm ci && npm test && npm run lint && npm run build
```

## Phase 3 limitations

Working: Phase 1 health stack, Phase 2 auth, GitHub App OAuth/link, installations, repository discovery/connections, webhooks, `/github` UI.

## Phase 4 limitations

Working: execution jobs, worker queue, Docker sandbox isolation, fixture checkout, constrained commands, logs/cancel/timeout, `/executions` UI.

## Phase 5 limitations

Working: agent runs/steps, FakeLLM deterministic loop, OpenAI provider hook, tool security, `/agent` UI.

Not yet: git commit/push, PR creation, approval flow, WebSocket workspace, browser automation.

## Phase roadmap

| Phase | Deliverable |
|-------|-------------|
| 1 | Monorepo, Compose, health endpoints, landing page |
| 2 | Auth, models, sessions |
| 3 | GitHub App + repository connections |
| 4 | Secure sandbox runtime + worker execution |
| 5 | Coding agent state machine + tools |
| 6 | WebSocket workspace UI |
| 7 | Diff, approval, PR |
| 8 | Playwright browser tools |
| 9 | Observability + E2E |

## Security

See [docs/security.md](docs/security.md). Sandboxes must never receive GitHub App private keys, DB credentials, Redis secrets, or LLM API keys.

## License

Proprietary / TBD.
