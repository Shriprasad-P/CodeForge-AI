# Production deployment notes

## Compose environments

The base `docker-compose.yml` keeps PostgreSQL and Redis on the private
Compose network and requires credentials to be supplied by the environment.
For local development, copy `.env.example` to `.env` and explicitly add the
development-only host-port override:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Do not run the development override as production. Production startup also
fails closed when `APP_ENV=production` uses the known database password,
the default session secret, missing database credentials, or an insecure
cookie setting.

Stage 1 keeps the existing shared PostgreSQL connection configuration for the
API and worker; separate least-privilege database roles are intentionally
deferred with the broader database architecture work.

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
