# AgentDock

Secure cloud coding-agent platform. Users connect a GitHub App, pick a repository, describe a task, and an AI agent works inside an isolated sandbox — then opens a pull request after approval.

> Phase 1 status: monorepo, Docker Compose (Postgres + Redis), FastAPI health/ready/metrics, Next.js landing + live status.

![Screenshot placeholder](docs/assets/screenshot-placeholder.svg)

## Architecture

See [docs/architecture.md](docs/architecture.md) and [docs/adr/0001-architecture.md](docs/adr/0001-architecture.md).

```text
agentdock/
├── apps/
│   ├── web/          # Next.js (App Router)
│   ├── api/          # FastAPI
│   ├── agent/        # LLM agent (Phase 5)
│   └── worker/       # Job worker (Phase 4+)
├── packages/         # shared-types, agent-tools, sandbox-sdk, github-client
├── infrastructure/
├── tests/
├── docs/
├── docker-compose.yml
└── .env.example
```

## Quick start (local)

### Prerequisites

- Docker Desktop / Docker Engine + Compose
- Python 3.12 or 3.13 (3.14 not yet supported by pydantic-core wheels)
- Node.js 22+

### 1. Environment

```bash
cp .env.example .env
```

### 2. Infrastructure only (recommended for day-to-day)

```bash
docker compose up -d postgres redis
```

### 3. API

```bash
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Verify:

```bash
curl -s http://localhost:8000/api/health | jq
curl -s http://localhost:8000/api/ready | jq
curl -s http://localhost:8000/api/metrics
```

### 4. Web

```bash
cd apps/web
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

### Full stack via Compose

```bash
docker compose up --build
```

- Web: http://localhost:3000  
- API docs: http://localhost:8000/docs  

## Tests

```bash
# API
cd apps/api && source .venv/bin/activate && pytest -q

# Web
cd apps/web && npm test
```

## Phase roadmap

| Phase | Deliverable |
|-------|-------------|
| 1 | Monorepo, Compose, health endpoints, landing page |
| 2 | Auth, models, sessions |
| 3 | GitHub App |
| 4 | Docker sandbox provider |
| 5 | Agent state machine + tools |
| 6 | WebSocket workspace UI |
| 7 | Diff, approval, PR |
| 8 | Playwright browser tools |
| 9 | Observability + E2E |

## Security

See [docs/security.md](docs/security.md). The agent runs outside the sandbox; sandboxes never receive GitHub App private keys, DB credentials, or LLM secrets.

## License

Proprietary / TBD.