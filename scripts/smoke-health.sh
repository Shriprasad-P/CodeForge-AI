#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> AgentDock Phase 1 smoke"

API_URL="${API_URL:-http://localhost:8000}"

echo "Health: $API_URL/api/health"
curl -fsS "$API_URL/api/health" | python3 -m json.tool

echo "Ready: $API_URL/api/ready"
curl -fsS "$API_URL/api/ready" | python3 -m json.tool

echo "Metrics:"
curl -fsS "$API_URL/api/metrics"
echo
echo "OK"
