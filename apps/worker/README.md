# AgentDock Worker

Privileged control-plane service that consumes Redis execution jobs and runs them in isolated Docker sandboxes.

- Mounts the Docker socket (never passed into sandboxes)
- Clones/copies repositories on the worker host, then copies trees into sandboxes
- Default checkout mode: `fixture` (`/app/fixtures/sample-repo`) for credential-free local boots

See [docs/sandbox.md](../../docs/sandbox.md).
