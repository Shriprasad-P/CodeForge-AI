#!/bin/sh
set -e
# Single-replica Compose: migrate then serve. Production should run migrations as a one-shot job.
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
