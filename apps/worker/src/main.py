from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow importing the API package and sandbox SDK when run from Compose or locally.
_ROOT = Path(__file__).resolve().parents[2]
_API = _ROOT / "api"
_SDK = _ROOT.parent / "packages" / "sandbox-sdk"
for path in (_API, _SDK, Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(path))

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.redis import close_redis, init_redis
from app.db.session import close_db, init_db
from app.services.queue import dequeue_work
from sandbox_sdk.docker_provider import DockerSandboxProvider

from src.agent.loop import process_agent_run
from src.processor import process_job, reconcile_stale_jobs

configure_logging()
logger = get_logger(__name__)


async def worker_loop() -> None:
    settings = get_settings()
    await init_db()
    await init_redis()
    provider = DockerSandboxProvider()
    await reconcile_stale_jobs(provider)
    logger.info(
        "worker.started",
        concurrency=settings.worker_concurrency,
        checkout_mode=settings.sandbox_checkout_mode,
        llm_provider=settings.llm_provider or "unset",
    )

    sem = asyncio.Semaphore(settings.worker_concurrency)
    tasks: set[asyncio.Task] = set()

    async def _run_execution(job_id) -> None:
        async with sem:
            await process_job(job_id, provider)

    async def _run_agent(run_id) -> None:
        async with sem:
            await process_agent_run(run_id, provider)

    try:
        while True:
            item = await dequeue_work(timeout_seconds=5)
            if item is None:
                await asyncio.sleep(0.1)
                continue
            kind, work_id = item
            if kind == "agent":
                task = asyncio.create_task(_run_agent(work_id))
            else:
                task = asyncio.create_task(_run_execution(work_id))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    finally:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await close_redis()
        await close_db()
        logger.info("worker.stopped")


def main() -> None:
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
