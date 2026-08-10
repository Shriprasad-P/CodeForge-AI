# Test Plan

## Phase 1

- [x] API unit/integration: `/api/health`, `/api/ready`, `/api/metrics`
- [x] DB/Redis check helpers
- [x] Frontend: SystemStatus happy path + API down + dependency unhealthy
- [x] Compose smoke: `docker compose up --build` then curl health/ready

## Phase 2

- [x] Auth register/login/logout/me
- [x] Duplicate email / invalid email / weak password
- [x] Ownership isolation for agent sessions
- [x] Alembic upgrade + downgrade
- [x] Frontend login success/failure + dashboard + logout
- [x] CI Postgres/Redis services + `alembic upgrade head`

## Phase 3

- [x] GitHub App JWT (RS256) + invalid key handling
- [x] OAuth connect/callback state (invalid, expired, replay)
- [x] Installations list + cross-user isolation
- [x] Repository discovery (mocked) + connect/disconnect ownership
- [x] Webhook signature + installation lifecycle + delivery idempotency
- [x] Frontend: not configured / connect / list / connect repo / disconnect / error / unauthorized / loading
- [x] Fresh clone without GitHub credentials still boots
- [ ] Optional live GitHub App E2E (local credentials only; not required in CI)

## Phase 4

- [x] Execution job API create/list/get/logs/cancel + IDOR
- [x] Path traversal / shell argv rejection
- [x] Worker claim + fixture checkout + remote sanitization unit tests
- [x] Docker sandbox integration (non-root, no socket, no secrets, timeout, cleanup)
- [x] Frontend executions UI tests
- [x] Compose worker service + sandbox image build
- [ ] Optional live GitHub installation clone (credentials)

## Phase 5

- [x] Agent run API + ownership/cancel
- [x] FakeLLM deterministic sandbox E2E (inspect/edit/test/diff)
- [x] Tool path escape tests + prompt-injection secret non-leak
- [x] Frontend agent panel tests
- [ ] Optional live OpenAI coding task

## Phase 6

- [x] WebSocket auth / anonymous / IDOR / revoked session
- [x] Event publisher sequence + malformed rejection
- [x] FakeLLM lifecycle event sequence via Redis Pub/Sub
- [x] Docker incremental command output streaming
- [x] Frontend live activity / XSS-safe output / reconnect / cancel
- [x] Docs: `docs/realtime.md`
- [ ] Optional multi-replica WS soak (Compose remains single API)

## Phase 7

- [x] Owner-only approval, rejection, duplicate approval, and cancellation boundary
- [x] SHA-256 diff and base-commit integrity checks
- [x] Deterministic local Git commit and push to a bare remote
- [x] Mocked PR creation and duplicate-delivery idempotency
- [x] Publication event protocol and frontend approval controls
- [ ] Live GitHub App installation and PR creation (requires repository credentials)
