# Demo and screenshot plan

This is a deterministic, local recording plan for AgentDock. It uses the repository fixture, `LLM_PROVIDER=fake`, a local Git remote, and the GitHub API stub. It is intended to show real application state, not fabricated outcomes.

## Scenario

Use a small task that produces a visible code and test change:

> Add request validation to the `/users` endpoint and add tests.

The expected path is:

`Connect repository → enter task → inspect → edit → validate → review diff → approve → create PR`

## Screenshot-ready states

1. **Landing:** AgentDock identity, security promise, workflow preview, and trust-boundary diagram.
2. **Overview:** connected repository count, active/completed runs, and recent workflow outcome.
3. **Active run:** repository/task header, durable execution timeline, live operational activity, and separate command output.
4. **Validation passed:** `Validating` completed with the result visible before approval.
5. **Diff review:** changed-file navigation, additions/deletions, bounded-preview warning where applicable, artifact status, and base SHA.
6. **Approval:** repository, base commit, changed-file count, validation, and artifact integrity appear beside **Approve & Create PR** and **Reject**.
7. **Publication result:** branch, commit, and pull-request link after the trusted publication worker completes.

## 45–75 second recording

| Time | Product state |
| --- | --- |
| 0–8s | Landing page or overview |
| 8–15s | Select the fixture repository and enter the task |
| 15–30s | Agent inspects, edits, and streams operational activity |
| 30–40s | Validation completes successfully |
| 40–52s | Navigate changed files and review the diff |
| 52–58s | Approve the immutable artifact |
| 58–70s | Show branch/commit and the created PR request |

Keep the browser at a developer-console width so the timeline and diff remain readable. Do not include terminal secrets or internal exception details in the recording.
