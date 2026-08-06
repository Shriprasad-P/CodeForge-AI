# Test Plan

## Phase 1

- [x] API unit/integration: `/api/health`, `/api/ready`, `/api/metrics`
- [x] DB/Redis check helpers
- [x] Frontend: SystemStatus happy path + API down
- [ ] Compose smoke: `docker compose up --build` then curl health/ready

## Later phases

- Auth / authorization
- GitHub webhook signature verification
- Sandbox path traversal + cleanup
- Agent approval + no default-branch push
- E2E: edit → test → approve → PR → destroy sandbox
