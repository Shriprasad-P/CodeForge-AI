"""Bounded executor for blocking worker-side operations."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable


# Docker SDK calls and local git/file operations are synchronous. Keep them
# away from the event loop and cap the number of worker threads explicitly.
_BLOCKING_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agentdock-blocking")


async def run_blocking(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_BLOCKING_EXECUTOR, partial(fn, *args, **kwargs))


def shutdown_blocking_executor() -> None:
    """Release bounded worker threads during graceful shutdown."""
    _BLOCKING_EXECUTOR.shutdown(wait=True, cancel_futures=True)
