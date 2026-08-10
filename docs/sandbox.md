# Sandbox Runtime (Phase 4)

AgentDock runs repository commands only inside ephemeral Docker sandboxes created by the **worker**.

## Trust boundaries

| Component | Trust | Notes |
|-----------|-------|-------|
| API | Control plane | Auth, job create/cancel, no Docker socket |
| Worker | Privileged control plane | May mount Docker socket; holds GitHub App credentials for clone |
| Sandbox | Untrusted | Repository code; no secrets; no Docker socket |
| Postgres / Redis | Control plane | Not on the sandbox network |

The worker is privileged. Sandbox containers are not.

## Isolation defaults

| Control | Value |
|---------|-------|
| User | `10001:10001` (non-root) |
| Privileged | `false` |
| Capabilities | all dropped (`cap_drop=ALL`) |
| `no-new-privileges` | enabled |
| Root FS | writable for practical `put_archive` (system paths remain root-owned; non-root) |
| Host mounts | none (no repo bind mounts, no docker.sock) |
| Network | **disabled** (`SANDBOX_NETWORK_DISABLED=true`) |
| CPU | `SANDBOX_CPU_LIMIT` (default 1.0) |
| Memory | `SANDBOX_MEMORY_LIMIT` (default 512m) |
| PIDs | `SANDBOX_PIDS_LIMIT` (default 256) |
| Timeout | `SANDBOX_TIMEOUT_SECONDS` (default 120) |
| Output | `SANDBOX_MAX_OUTPUT_BYTES` (default 256000), truncated flag |

Disk quotas are not enforced by plain Docker. Documented residual risk.

## Checkout security

Clone happens on the **worker host**, not inside the sandbox:

1. Mint a short-lived GitHub installation token (GitHub mode)
2. `git clone` to a temp directory on the worker
3. Rewrite `origin` to a credential-free HTTPS URL
4. Copy the tree into the sandbox via `put_archive`
5. Delete the temp directory and drop the token from memory

Fixture mode (`SANDBOX_CHECKOUT_MODE=fixture`) copies `fixtures/sample-repo` for local/CI without GitHub credentials.

## Secret allowlist

Sandbox env is constructed explicitly:

```text
CI=true
AGENTDOCK_SANDBOX=true
HOME=/workspace
PATH=...
```

Never inherited: `DATABASE_URL`, `REDIS_URL`, `SESSION_SECRET`, GitHub App secrets, webhook secret, LLM keys, `DOCKER_HOST`.

## Command model

Commands are argv arrays executed with `exec` (no shell):

```json
{ "command": ["python", "-m", "pytest", "-q"] }
```

Shell binaries (`bash`, `sh`, …) are rejected at the API. Working directories must be relative and cannot contain `..`.

## Queue

Redis list `agentdock:executions` (LPUSH / BRPOP). Job rows in Postgres are the source of truth; workers atomically claim `queued → starting`. Duplicate queue messages do not re-run terminal jobs.

## Cleanup

Containers are labelled `agentdock.sandbox=true` and `agentdock.execution_id=<uuid>`. Destroy runs in `finally`. Worker startup reconciles stale non-queued active jobs and removes labelled orphans.

## Network policy (Phase 4)

**Option A (default):** sandbox network disabled. Package installs that need network will fail inside the sandbox. Safer for local Phase 4.

Outbound network can be enabled later deliberately; that increases SSRF/exfiltration risk and does not claim cloud-metadata safety unless additional controls exist.

## Production limitations

- Docker socket on the worker expands blast radius if the worker is compromised
- No nested Docker / no repository Dockerfiles
- No hard disk quota
- Cloud metadata (169.254.169.254) protection is a production requirement when network is enabled — not claimed for Phase 4 defaults
- Live private-repo clone requires GitHub App credentials

## Local image

```bash
docker build -t agentdock-sandbox:local -f infrastructure/sandbox/Dockerfile infrastructure/sandbox
```
