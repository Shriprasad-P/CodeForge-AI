from __future__ import annotations

from uuid import UUID

from app.core.config import get_settings
from app.db.redis import get_redis


async def enqueue_execution(job_id: UUID) -> None:
    settings = get_settings()
    redis = get_redis()
    await redis.lpush(settings.execution_queue_key, f"execution:{job_id}")


async def enqueue_agent_run(run_id: UUID) -> None:
    settings = get_settings()
    redis = get_redis()
    await redis.lpush(settings.agent_queue_key, f"agent:{run_id}")


async def enqueue_publication(run_id: UUID) -> None:
    settings = get_settings()
    redis = get_redis()
    await redis.lpush(settings.agent_queue_key, f"publication:{run_id}")


async def dequeue_work(timeout_seconds: int = 5) -> tuple[str, UUID] | None:
    """Pop from execution or agent queues. Returns (kind, id)."""
    settings = get_settings()
    redis = get_redis()
    item = await redis.brpop(
        [settings.execution_queue_key, settings.agent_queue_key],
        timeout=timeout_seconds,
    )
    if not item:
        return None
    _, raw = item
    if raw.startswith("execution:"):
        return "execution", UUID(raw.removeprefix("execution:"))
    if raw.startswith("agent:"):
        return "agent", UUID(raw.removeprefix("agent:"))
    if raw.startswith("publication:"):
        return "publication", UUID(raw.removeprefix("publication:"))
    # Backward-compatible bare UUID = execution
    return "execution", UUID(raw)
