# GitHub App Setup

Phase 3 will implement the GitHub App flow. Prepare credentials early:

1. Create a GitHub App with permissions:
   - Repository contents: read and write
   - Pull requests: read and write
   - Issues: read
   - Metadata: read
2. Generate a private key (`.pem`) and store it outside the repo (see `.env.example`).
3. Set webhook URL to `https://<api-host>/api/github/webhook` with a strong webhook secret.
4. Record App ID, Client ID, Client Secret, and App slug in `.env`.

Do not commit private keys.