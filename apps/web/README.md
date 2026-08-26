# AgentDock web workspace

The web app is the Next.js App Router client for AgentDock. It exposes the user-facing path from GitHub connection to agent run, diff review, approval, and pull-request result.

## Routes

- `/` — product landing page and control-plane status
- `/dashboard` — repositories, workflow summary, and recent runs
- `/github` — GitHub App installations and repository connections
- `/agent` — bounded coding-agent workspace with realtime recovery
- `/executions` — direct sandbox validation jobs
- `/login`, `/register` — browser session entry points

## Local development

From this directory:

```bash
npm ci
npm run dev
```

Set `NEXT_PUBLIC_API_URL` when the API is not at `http://localhost:8000`. The WebSocket URL is derived from that API URL unless `NEXT_PUBLIC_WS_URL` is set.

## Verification

```bash
npm test
npx tsc --noEmit
npm run lint
npm run build
```

The client treats WebSocket activity as an enhancement over PostgreSQL-backed REST state: reconnects resynchronize queries and active runs fall back to polling when the socket is unavailable.
