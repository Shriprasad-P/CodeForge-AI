# Approved publication (Phase 7)

AgentDock separates untrusted agent execution from trusted repository publication.
The sandbox produces a bounded diff and validation result, then pauses in
`awaiting_approval`. Only the authenticated owner can approve that exact,
persisted diff.

Approval records the approving user and timestamp, fingerprints the diff with
SHA-256, and queues a publication job. The trusted worker clones the default
branch with a short-lived GitHub App installation token, verifies the recorded
base commit, reapplies and refingerprints the patch, reruns validation in a
fresh network-disabled sandbox, then creates a deterministic branch and commit
using the fixed AgentDock author identity. It pushes the branch and discovers
or creates the matching pull request.

Postgres is authoritative for approval and publication state. Redis only queues
work and broadcasts live events. Browser connectivity is never required for a
run or publication job to continue. Duplicate queue delivery is safe after a
published marker, and retries discover an existing pull request by head/base
before creating another one.

GitHub credentials never enter the agent sandbox. Clone and push failures are
persisted as `publication_failed`; a base SHA mismatch is persisted as
`repository_changed` and is never auto-rebased.

## Local deterministic verification

Set `APP_ENV=test`, `PUBLICATION_TEST_REMOTE_URL` to a local bare Git remote, and
`PUBLICATION_MOCK_PRS=true`. The worker test performs a real commit and push to
that remote, verifies durable published state, and verifies duplicate delivery.
These settings are honored only in local/test/development environments.
