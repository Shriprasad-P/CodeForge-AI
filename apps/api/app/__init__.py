"""AgentDock API — application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
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
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(agent_sessions_router)
    app.include_router(github_router)
    app.include_router(executions_router)
    app.include_router(agent_runs_router)
    app.include_router(ws_agent_router)
    return app


app = create_app()
