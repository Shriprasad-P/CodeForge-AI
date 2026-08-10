FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/api:/app/packages/sandbox-sdk:/app/worker

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin worker

# Docker CLI only (socket mounted at runtime). Sandboxes never get this socket.
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker

COPY packages/sandbox-sdk /app/packages/sandbox-sdk
COPY apps/api/requirements.txt /app/api/requirements.txt
COPY apps/worker/requirements.txt /app/worker/requirements.txt
RUN pip install --no-cache-dir \
      -r /app/api/requirements.txt \
      docker \
      -e /app/packages/sandbox-sdk

COPY apps/api /app/api
COPY apps/worker /app/worker
COPY fixtures /app/fixtures

# Worker is a privileged control-plane component (Docker socket). Runs as root
# only because the Docker socket group often requires it on Desktop; do not
# confuse this with sandbox containers (non-root, no socket).
WORKDIR /app/worker

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import redis,os; redis.from_url(os.environ['REDIS_URL']).ping()" || exit 1

CMD ["python", "-m", "src.main"]
