# GitHub App Setup (Phase 3)

AgentDock uses a **GitHub App** (not a long-lived personal access token) for installation-scoped repository access.

## Create the App

1. GitHub → Settings → Developer settings → GitHub Apps → New GitHub App.
2. Suggested fields:
   - **GitHub App name:** `AgentDock` (or `AgentDock-dev` for local)
   - **Homepage URL:** `http://localhost:3000`
   - **Callback URL:** `http://localhost:8000/api/github/callback`
   - **Setup URL (optional):** `http://localhost:8000/api/github/setup`
   - **Webhook URL:** `http://localhost:8000/api/github/webhooks` (use a tunnel such as ngrok for local delivery)
   - **Webhook secret:** generate a strong random string; set `GITHUB_WEBHOOK_SECRET`
3. Generate a private key (`.pem`). Store it outside git, or paste into `GITHUB_APP_PRIVATE_KEY` with `\n` newlines.
4. Copy App ID, Client ID, Client Secret, and App slug into `.env`.

## Permissions

Repository permissions:

| Permission | Access | Why |
|------------|--------|-----|
| **Metadata** | Read-only | List installation repositories and read safe repo metadata |

Phase 3 minimum:

 - Metadata: read-only

Phase 7 publication additionally requires:

 - Contents: read and write — clone the default branch and push the run branch
 - Pull requests: read and write — discover or create the pull request

Do **not** request:

- Administration: write

The worker uses short-lived installation tokens and never exposes them to the browser or agent sandbox.

Account permissions: none required beyond the App’s user-to-server OAuth for identity linking.

## Webhook events

Subscribe to:

- `installation`
- `installation_repositories`

AgentDock verifies `X-Hub-Signature-256` with constant-time compare and idempotently records `X-GitHub-Delivery`.

## Environment variables

```text
GITHUB_APP_ID=
GITHUB_APP_SLUG=agentdock
GITHUB_APP_CLIENT_ID=
GITHUB_APP_CLIENT_SECRET=
GITHUB_APP_PRIVATE_KEY=        # PEM with \n escapes, or use path below
GITHUB_APP_PRIVATE_KEY_PATH=   # e.g. ./secrets/github-app.pem
GITHUB_WEBHOOK_SECRET=       # empty when the App has no public webhook configured
GITHUB_CALLBACK_URL=http://localhost:8000/api/github/callback
GITHUB_SETUP_URL=http://localhost:8000/api/github/setup
GITHUB_FRONTEND_SUCCESS_URL=http://localhost:3000/github
```

If the App credentials are unset, the API still starts. `/api/github/status` reports
`configured: false` and the UI shows “GitHub integration is not configured”.

Webhook delivery is optional for local App/OAuth use. If no public HTTPS webhook URL
is supplied to the manifest bootstrap, the App can still be configured for API access;
webhook verification remains disabled until `GITHUB_WEBHOOK_SECRET` is populated.

## Connection flow

```text
Login → /github → Connect GitHub
  → GitHub OAuth (link identity to existing AgentDock user)
  → GitHub App install (personal account or org)
  → Setup callback claims installation
  → List repos via installation token (temporary, server-side only)
  → Connect repository (persists metadata)
  → Disconnect repository (local only; does not uninstall the App)
```

## Security notes

- Private key, client secret, webhook secret, and installation tokens never leave the API.
- Installation tokens are generated on demand and are not persisted or returned to the browser.
- OAuth `state` is bound to the authenticated user in Redis, single-use, and TTL-limited.
- All installation/connection queries are scoped to `current_user.id`.

## Local testing without live GitHub

Unit/integration tests mock GitHub HTTP at the client boundary and generate ephemeral RSA keys. Live App credentials are optional.

## Developer App Manifest bootstrap

For local Phase 7 verification, run:

```bash
python scripts/github_app_bootstrap.py
```

This development-only CLI opens GitHub's official App Manifest registration
page with AgentDock's minimal permissions, receives the one-time localhost
callback, exchanges the temporary code, and stores credentials in `.env` and
`.local-secrets/github-app.pem` with restrictive permissions. It never prints
credential values and refuses to run outside development, local, or test
environments.

After GitHub confirms registration, install the App using “Only select
repositories” and choose `agentdock-live-test`. The CLI cannot bypass that
authorization step.

## Production considerations

- Use HTTPS callback/webhook/setup URLs.
- Rotate webhook secret and private key if leaked.
- Prefer mounting the PEM via secret volume (`GITHUB_APP_PRIVATE_KEY_PATH`) over baking into images.
- Do not commit real secrets or `.pem` files.
