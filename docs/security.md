# Security Threat Model (MVP)

## Assets

- GitHub App private key
- LLM API keys
- Database credentials
- Short-lived GitHub installation tokens
- User repository source code in sandboxes

## Trust zones

1. **Control plane** (API, agent, worker) — holds secrets; never mounts Docker socket into sandboxes.
2. **Sandbox** — untrusted code execution; receives only a short-lived clone/push token.
3. **Browser** — untrusted; session cookies / JWTs only.

## Threats and mitigations

| Threat | Mitigation |
|--------|------------|
| Sandbox escapes to host | Non-root, no privileged, no Docker socket, resource limits, unique network |
| Secret exfiltration via agent tools | Secrets never injected into sandbox env except short-lived GitHub token |
| Path traversal | Validate all paths stay under repo root |
| Token reuse after session | Destroy sandbox; revoke/drop token from env |
| Webhook forgery | HMAC signature verification |
| Direct push to default branch | Agent policy + server-side branch checks |
| Dangerous commands | Approval modes + allow/deny lists |

## Phase 1 status

Phase 1 ships health/ready checks only. Auth and sandbox isolation land in Phases 2–4.