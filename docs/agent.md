# Coding Agent (Phase 5)

AgentDock’s coding agent is a **bounded tool loop** on the worker control plane. Repository code still executes only inside sandboxes.

## Flow

```text
POST /api/agent-runs
  → Redis agent queue
  → Worker claims run
  → Fixture/GitHub checkout on worker host
  → Sandbox create + copy tree
  → LLM chooses tools (validated)
  → Tools run in sandbox
  → finish → capture git status/diff + optional validation
  → Sandbox destroyed
```

## State machine

`queued → planning → running → validating → succeeded|failed|cancelled|timed_out|step_limit_reached`

## Tools

`list_files`, `read_file`, `search_code`, `write_file`, `apply_patch`, `run_command`, `git_status`, `git_diff`, `finish`

All paths are workspace-relative. `.git` is blocked for direct access. `run_command` is argv-only with an allowlisted executable family.

## Providers

| Provider | When |
|----------|------|
| `fake` | CI / local deterministic loops (`FakeLLMProvider`) |
| `openai` | Live runs with `OPENAI_API_KEY` |

Soft-disable: if LLM is not configured, `/api/agent-runs/status` reports `configured: false` and create returns **503**. The platform still boots.

## Limits (defaults)

| Limit | Default |
|-------|---------|
| `AGENT_MAX_STEPS` | 20 |
| `AGENT_MAX_TOOL_CALLS` | 40 |
| `AGENT_MAX_RUNTIME_SECONDS` | 600 |
| `AGENT_MAX_CONTEXT_CHARS` | 48000 |
| `AGENT_MAX_FILE_READ_BYTES` | 64000 |
| `AGENT_MAX_SEARCH_RESULTS` | 40 |
| `AGENT_MAX_TOOL_OUTPUT_CHARS` | 16000 |
| `AGENT_MAX_DIFF_CHARS` | 80000 (tool patch input limit) |
| `AGENT_MAX_DIFF_PREVIEW_CHARS` | 80000 |
| `AGENT_MAX_PUBLICATION_ARTIFACT_BYTES` | 8000000 |

## Success semantics

`succeeded` requires `finish` plus optional validation returning exit 0 when a validation command was requested. Diff/changed files come from git tooling, not model claims.

## Prompt injection

Repository files are treated as untrusted data. System prompt forbids following in-repo override instructions. LLM keys never enter the sandbox.

## Known limitations

- No commit/push/PR
- Network remains disabled in sandboxes
- Live model quality varies; CI uses FakeLLM
- Real-time UI: see [realtime.md](realtime.md)
