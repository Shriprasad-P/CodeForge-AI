# AgentDock operations checklist

This is an engineering runbook for the existing API, worker, PostgreSQL,
Redis, Docker sandbox, and GitHub App workflow.

## Configuration and startup

- Inject a unique `SESSION_SECRET`, production `DATABASE_URL`, and Redis URL.
- Keep GitHub App private keys and webhook secrets in the control plane only;
  never pass them to sandbox containers.
- Configure `EXPECTED_ALEMBIC_REVISION` and run `alembic upgrade head` before
  starting API traffic.
- Start PostgreSQL and Redis, then the API and at least one worker.  In
  production set `WORKER_READINESS_REQUIRED=true` so `/api/ready` fails when
  no fresh worker heartbeat exists.
- GitHub and LLM settings are reported as capabilities.  Make them required
  in deployment configuration only when those workflows are enabled.

## Probes and observability

- `/api/health` is liveness only and must not be used as dependency health.
- `/api/ready` checks PostgreSQL, Redis, migration compatibility, and the
  configured worker requirement.
- Scrape `/api/metrics`.  PostgreSQL-derived workflow and outbox gauges are
  authoritative; Redis telemetry counters are best effort.
- Search JSON logs by `request_id` and the durable
  `workflow_correlation_id`.  Use `outbox_event_id`, `delivery_attempt`,
  `claim_ref`, and `publication_attempt_id` to follow retries without ever
  logging claim tokens.
- Logs must remain bounded and must not contain cookies, credentials, private
  keys, prompts, repository source, or unbounded command output.

## Recovery checklist

1. Check `/api/ready`, PostgreSQL, Redis, and the worker heartbeat.
2. Locate the run by `agent_run_id` or `execution_job_id` and follow its
   durable state transitions in PostgreSQL.
3. Inspect the corresponding outbox row, attempt count, `last_error`, and
   `next_attempt_at`.
4. Allow lease reconciliation to recover expired work.  Do not manually
   replay a publication unless the approval artifact, base SHA, and
   repository authorization still match.
5. For repository revocation, expect a durable `repository_revoked` terminal
   state and no branch, push, or pull request.

## GitHub, sandbox, and data safety

- Use HTTPS and secure, HttpOnly session cookies in production.
- GitHub installation tokens are short-lived and must stay in trusted worker
  memory; clone credentials are supplied through `GIT_ASKPASS` and removed
  after use.
- Sandboxes use resource limits and disabled networking by default.  The
  Docker socket, database credentials, Redis credentials, session secrets,
  GitHub keys/tokens, and LLM keys must never be mounted or exposed.
- PostgreSQL backups must include workflow, outbox, artifact, and approval
  records.  Test restore procedures before relying on them.

## Migrations and rollback

- Apply migrations forward with Alembic; never rewrite an applied migration.
- Confirm `alembic current` equals `EXPECTED_ALEMBIC_REVISION` after deploy.
- Roll back application code only when it remains compatible with the current
  schema.  Take a database backup before destructive schema work.

## Known limitations

- Redis Pub/Sub events are ephemeral; clients recover durable state through
  REST snapshots.
- Process-local metrics can reset on restart.  Cross-process worker counters
  are best-effort Redis telemetry and are not workflow truth.
- The deterministic end-to-end test uses a local Git remote, fake LLM, and
  GitHub stub; it does not prove live GitHub credentials or provider quotas.
