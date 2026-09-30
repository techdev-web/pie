"""FastAPI application for PIE Phase 0–1."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.routes import cases, documents, evidence, health, jobs
from packages.config import get_settings
from packages.observability import configure_logging, get_logger

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log.info("api_starting")
    yield
    from packages.domain.db import dispose_engine

    await dispose_engine()
    log.info("api_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="PIE API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(cases.router, prefix="/v1")
    app.include_router(documents.router, prefix="/v1")
    app.include_router(jobs.router, prefix="/v1")
    app.include_router(evidence.router, prefix="/v1")
    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "apps.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.pie_env == "development",
    )
