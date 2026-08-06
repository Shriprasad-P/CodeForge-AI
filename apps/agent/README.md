# AgentDock Agent Service

Runs the coding-agent state machine (LangGraph / custom). Speaks to the API and
sandbox via tools. **Never** mounts the Docker socket or holds DB credentials
inside a user sandbox.

Phase 5 implementation.