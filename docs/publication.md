# Approved publication (Phase 7)

AgentDock separates untrusted agent execution from trusted repository publication.
The sandbox produces a complete Git-native publication artifact and validation
result, then pauses in `awaiting_approval`. Only the authenticated owner can
approve that exact, persisted artifact. The UI receives an independently
bounded preview; preview truncation never changes the stored artifact.

Approval records the approving user, artifact hash/version, base SHA, and
timestamp. The trusted worker clones the default branch with a short-lived
GitHub App installation token, verifies the recorded base commit, verifies and
reapplies the full binary-aware artifact, recaptures and compares the artifact
and manifest, reruns validation in a fresh network-disabled sandbox, then
creates a deterministic branch and commit using the fixed AgentDock author
identity. It pushes the branch and discovers or creates the matching pull
request.

Postgres is authoritative for approval and publication state. Redis only queues
work and broadcasts live events. Browser connectivity is never required for a
run or publication job to continue. Duplicate queue delivery is safe after a
published marker, and retries discover an existing pull request by head/base
before creating another one.

GitHub credentials never enter the agent sandbox. Clone and push failures are
persisted as `publication_failed`; a base SHA mismatch is persisted as
`repository_changed` and is never auto-rebased.

The worker startup reconciler requeues approved work and returns abandoned
`publishing` claims to the approved state after a bounded lease. Each claim has
a durable token and heartbeat, so an old worker cannot overwrite a reclaimed
publication. If Redis is
unavailable during approval, the approved state remains durable in Postgres and
is requeued on the next worker startup.

Runs created before artifact version 1 are legacy and cannot be approved or
published. Artifacts over `AGENT_MAX_PUBLICATION_ARTIFACT_BYTES` fail closed;
`AGENT_MAX_DIFF_PREVIEW_CHARS` only controls the UI preview.
An explicit final `validation_command` is required for an agent run to become
approvable; ordinary exploratory commands do not create a validation binding.

## Local deterministic verification

Set `APP_ENV=test`, `PUBLICATION_TEST_REMOTE_URL` to a local bare Git remote, and
`PUBLICATION_MOCK_PRS=true`. The worker test performs a real commit and push to
that remote, verifies durable published state, verifies duplicate delivery, and
checks that a tampered artifact cannot publish.
These settings are honored only in local/test/development environments.
