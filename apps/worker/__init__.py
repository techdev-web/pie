"""arq worker entrypoint."""

from __future__ import annotations

import uuid

from arq.connections import RedisSettings

from packages.config import get_settings
from packages.domain.db import get_session_factory
from packages.observability import configure_logging, get_logger
from packages.pipeline import run_document_pipeline

log = get_logger("worker")


async def process_document(ctx: dict, job_id: str) -> str:
    configure_logging()
    log.info("process_document_start", job_id=job_id)
    factory = get_session_factory()
    async with factory() as session:
        await run_document_pipeline(session, uuid.UUID(job_id))
    log.info("process_document_done", job_id=job_id)
    return "ok"


class WorkerSettings:
    functions = [process_document]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 2
    job_timeout = 600


def run() -> None:
    import sys

    from arq.cli import cli

    # `arq apps.worker.main.WorkerSettings`
    sys.argv = ["arq", "apps.worker.main.WorkerSettings"]
    configure_logging()
    cli()
