"""AgentDock API — application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
import time
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.agent_runs import router as agent_runs_router
from app.api.agent_sessions import router as agent_sessions_router
from app.api.auth import router as auth_router
from app.api.executions import router as executions_router
from app.api.github import router as github_router
from app.api.health import router as health_router
from app.api.ws_agent import router as ws_agent_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.observability import bind_observability, clear_observability, metrics, normalize_request_id
from app.db.redis import close_redis, init_redis
from app.db.session import close_db, init_db

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info("api.starting", app=settings.app_name, env=settings.app_env)
    await init_db()
    await init_redis()
    yield
    await close_redis()
    await close_db()
    logger.info("api.stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.6.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_observability(request, call_next):
        request_id = normalize_request_id(request.headers.get("X-Request-ID"))
        clear_observability()
        bind_observability(request_id=request_id)
        started = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
            metrics.inc("agentdock_api_requests_total")
            if response.status_code >= 400:
                metrics.inc("agentdock_api_errors_total")
            return response
        except Exception as exc:  # noqa: BLE001
            metrics.inc("agentdock_api_requests_total")
            metrics.inc("agentdock_api_errors_total")
            logger.exception(
                "api.request.failed",
                error_class=type(exc).__name__,
                retryable=False,
            )
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            metrics.observe_duration("agentdock_api_request_duration_ms", duration_ms)
            if response is not None:
                response.headers["X-Request-ID"] = request_id
                logger.info(
                    "api.request.completed",
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=round(duration_ms, 2),
                )
            clear_observability()
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(agent_sessions_router)
    app.include_router(github_router)
    app.include_router(executions_router)
    app.include_router(agent_runs_router)
    app.include_router(ws_agent_router)
    return app


app = create_app()
