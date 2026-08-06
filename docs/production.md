# Production deployment notes

## Current (Phase 1)

Compose is for local development. Do not run the default Compose stack as production.

## Target production shape

- Managed Postgres (backups, encryption at rest)
- Managed Redis (TLS, ACLs)
- API / agent / worker as separate deployable units
- Sandbox provider with hard isolation (Firecracker / K8s / managed)
- Secrets via a vault or cloud secret manager — never bake `.pem` into images
- TLS termination at the edge; WebSocket sticky sessions if multi-instance API
- Horizontal worker autoscaling on queue depth
- Prometheus scrape of `/api/metrics`; structured logs to your aggregator

## Hardening checklist (later phases)

- [ ] Rotate GitHub App private keys
- [ ] Short session TTLs + sandbox TTL enforcement
- [ ] Network policies denying sandbox egress except git/package mirrors
- [ ] Image scanning in CI
- [ ] WAF / rate limits on auth and session creation
