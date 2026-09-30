"""arq worker entrypoint."""

from __future__ import annotations

import uuid

from arq.connections import RedisSettings

from packages.config import get_settings
from packages.domain.db import get_session_factory
from packages.observability import configure_logging, get_logger
from packages.pipeline import run_case_reconciliation
from packages.pipeline.runner import run_document_pipeline_with_reconcile

log = get_logger("worker")


async def process_document(ctx: dict, job_id: str) -> str:
    configure_logging()
    log.info("process_document_start", job_id=job_id)
    factory = get_session_factory()
    case_id = None
    async with factory() as session:
        case_id = await run_document_pipeline_with_reconcile(session, uuid.UUID(job_id))
    if case_id is not None:
        try:
            from apps.api.queue import enqueue_reconcile_case

            await enqueue_reconcile_case(case_id)
        except Exception as exc:
            log.warning("reconcile_enqueue_failed", case_id=str(case_id), error=str(exc))
            # Fallback: run inline if enqueue fails (e.g. redis hiccup)
            async with factory() as session:
                await run_case_reconciliation(session, case_id)
    log.info("process_document_done", job_id=job_id)
    return "ok"


async def reconcile_case(ctx: dict, case_id: str) -> str:
    configure_logging()
    log.info("reconcile_case_start", case_id=case_id)
    factory = get_session_factory()
    async with factory() as session:
        await run_case_reconciliation(session, uuid.UUID(case_id))
    log.info("reconcile_case_done", case_id=case_id)
    return "ok"


class WorkerSettings:
    functions = [process_document, reconcile_case]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 2
    job_timeout = 600


def run() -> None:
    import sys

    from arq.cli import cli

    sys.argv = ["arq", "apps.worker.main.WorkerSettings"]
    configure_logging()
    cli()
