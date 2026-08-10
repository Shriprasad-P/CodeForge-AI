# Security Threat Model (MVP)

## Assets

- User passwords / password hashes
- Auth session tokens (cookie)
- GitHub App private key (Phase 3+)
- LLM API keys (Phase 5+)
- Database credentials
- Short-lived GitHub installation tokens
- User repository source code in sandboxes

## Trust zones

1. **Control plane** (API, agent, worker) — holds secrets; never mounts Docker socket into sandboxes.
2. **Sandbox** — untrusted code execution; receives only a short-lived clone/push token.
3. **Browser** — untrusted; HttpOnly session cookie only (no tokens in localStorage).

## Phase 2 auth mitigations

| Threat | Mitigation |
|--------|------------|
| Password theft at rest | Argon2id hashes only; never returned by API |
| Session theft from DB | Store SHA-256(token), not raw cookie value |
| XSS token exfil | HttpOnly cookie; no localStorage auth |
| CSRF | SameSite=Lax + CORS allowlist; Secure cookies in non-dev |
| Brute force | Redis rate limit on login/register |
| User enumeration on login | Generic "Invalid email or password" |
| Ownership bypass | Queries always scope `user_id = current_user.id` |
| Sensitive logs | Log events without passwords/tokens/cookies |

## Phase 3 GitHub mitigations

| Threat | Mitigation |
|--------|------------|
| OAuth CSRF / account linking abuse | Redis-bound `state`, TTL, single-use consume |
| Callback replay | State deleted on first use |
| Webhook forgery | HMAC SHA-256 (`X-Hub-Signature-256`) + constant-time compare |
| Webhook retries | Idempotent upserts + `X-GitHub-Delivery` ledger |
| Private key / client secret leak to browser | Secrets only on API; never returned in JSON |
| Installation token leak | Minted server-side, not persisted, never sent to frontend |
| Repository IDOR | Connections/installations scoped to `current_user.id`; repo metadata fetched from GitHub |
| Stale install | Webhook suspend/delete clears or marks local rows |
| Unsafe redirects | Callback redirects only to configured frontend success URL |
| Unconfigured App crash | Missing GitHub env → soft-disable (`configured: false` / 503 on connect) |

## Phase 4 sandbox mitigations

| Threat | Mitigation |
|--------|------------|
| Host code execution | Commands run only in sandbox containers |
| Docker socket in sandbox | Never mounted; worker-only socket |
| Privilege escalation | non-root, `cap_drop=ALL`, `no-new-privileges`, non-privileged |
| Secret exfil via env | Explicit allowlist env; clone token never injected into sandbox |
| Token in git remote | Host-side clone + remote rewrite before copy |
| Path traversal workdir | Relative-only, reject `..` and absolute paths |
| Fork bomb / resource abuse | pids/memory/cpu limits + per-user active job cap |
| Output flooding | `SANDBOX_MAX_OUTPUT_BYTES` + truncate flag |
| Timeout evasion | External kill + timed_out status |
| IDOR | All job queries scoped to `user_id` |

See [sandbox.md](./sandbox.md) for residual risks (disk quota, worker socket privilege, network-off tradeoffs).

## Phase 5 agent mitigations

| Threat | Mitigation |
|--------|------------|
| Prompt injection from repo text | System prompt + treat files as untrusted data |
| LLM key in sandbox | Never injected; allowlist env only |
| Path traversal via tools | `safe_rel_path` rejects `..`, abs, `.git` |
| Unbounded autonomy | max steps / tool calls / runtime / context / outputs |
| Hallucinated test success | Success requires tool exit codes + finish |
| Host shell | argv-only allowlisted executables in sandbox |

## Phase 6 streaming mitigations

| Threat | Mitigation |
|--------|------------|
| Cross-user run subscription | Cookie auth + `agent_run.user_id` check before accept |
| Secrets in events | Never stream keys, cookies, tokens, DB/Redis URLs |
| XSS via command output | Plain-text UI only; no `dangerouslySetInnerHTML` |
| WS as control plane | Browser cannot invoke tools; cancel stays REST |
| Connection abuse | Per-user / per-run socket caps |
| Slow client blocking worker | Redis decouples; bounded outbound queues; drop ephemeral chunks |

## Phase 7 publication mitigations

| Threat | Mitigation |
|--------|------------|
| Unauthorized publication | Owner-scoped approval endpoint; atomic pending-to-approved transition |
| Diff substitution | SHA-256 fingerprint persisted with the run and checked before and after apply |
| Stale repository base | Exact recorded HEAD required; no automatic rebase |
| Duplicate commit/PR | Deterministic branch; durable commit/PR state; existing PR lookup on retry |
| Credential exposure | Short-lived installation token stays in trusted worker; remote URL sanitized; never copied to sandbox |
| Default-branch mutation | Publication always creates a run branch and pushes `branch:branch` |
| Publication command injection | Git operations use fixed argv; branch/title are normalized and bounded |

The developer GitHub App Manifest bootstrap is CLI-only, guarded to
development/local/test environments, uses a one-time random state, exchanges
the temporary code once, and stores the returned PEM in an ignored `0600` file.
It is not an API credential-generation route and is never part of production
runtime behavior.

## Broader threats

| Threat | Mitigation |
|--------|------------|
| Sandbox escapes to host | Non-root, no privileged, no Docker socket, resource limits (Phase 4) |
| Secret exfiltration via agent tools | Secrets never injected into sandbox env except short-lived GitHub token |
| Path traversal | Validate all paths stay under repo root |
| Direct push to default branch | Agent policy + server-side branch checks (Phase 7) |

## Production requirements

- Strong unique `SESSION_SECRET`
- `COOKIE_SECURE=true` behind HTTPS
- Rotate compromised sessions via revoke / TTL
- Run Alembic migrations as a one-shot deploy step (avoid multi-replica race)
- Do not expose Postgres/Redis ports publicly
- Store GitHub App PEM via secret mount; rotate webhook secret and private key if leaked
