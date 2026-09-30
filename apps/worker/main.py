"""arq worker entrypoint."""

from __future__ import annotations

import uuid

from arq.connections import RedisSettings
from sqlalchemy import select

from packages.config import get_settings
from packages.domain.db import get_session_factory
from packages.domain.models import Case
from packages.observability import configure_logging, get_logger
from packages.pipeline import run_case_reconciliation
from packages.pipeline.runner import run_document_pipeline_with_reconcile
from packages.reports import generate_case_report

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
            async with factory() as session:
                await run_case_reconciliation(session, case_id)
    log.info("process_document_done", job_id=job_id)
    return "ok"


async def reconcile_case(ctx: dict, case_id: str, force: bool = False) -> str:
    configure_logging()
    log.info("reconcile_case_start", case_id=case_id, force=force)
    factory = get_session_factory()
    async with factory() as session:
        await run_case_reconciliation(session, uuid.UUID(case_id), force=force)
    log.info("reconcile_case_done", case_id=case_id)
    return "ok"


async def analyze_case(
    ctx: dict,
    case_id: str,
    force: bool = False,
    generate_report: bool = True,
    actor: str = "system",
) -> str:
    configure_logging()
    log.info("analyze_case_start", case_id=case_id, force=force)
    factory = get_session_factory()
    cid = uuid.UUID(case_id)
    async with factory() as session:
        case = (
            await session.execute(select(Case).where(Case.id == cid))
        ).scalar_one_or_none()
        if case is None:
            log.warning("analyze_case_missing", case_id=case_id)
            return "missing"
        await run_case_reconciliation(session, cid, force=force)
        if generate_report:
            await generate_case_report(
                session,
                case_id=cid,
                tenant_id=case.tenant_id,
                triggered_by="worker.analyze",
                rebuild_reason="analyze:full" if force else "analyze:incremental",
                actor=actor,
            )
    log.info("analyze_case_done", case_id=case_id)
    return "ok"


async def generate_report(
    ctx: dict,
    case_id: str,
    actor: str = "system",
    rebuild_reason: str | None = None,
) -> str:
    configure_logging()
    log.info("generate_report_start", case_id=case_id)
    factory = get_session_factory()
    cid = uuid.UUID(case_id)
    async with factory() as session:
        case = (
            await session.execute(select(Case).where(Case.id == cid))
        ).scalar_one_or_none()
        if case is None:
            return "missing"
        await generate_case_report(
            session,
            case_id=cid,
            tenant_id=case.tenant_id,
            triggered_by="worker.generate_report",
            rebuild_reason=rebuild_reason or "queued",
            actor=actor,
        )
    log.info("generate_report_done", case_id=case_id)
    return "ok"


class WorkerSettings:
    functions = [process_document, reconcile_case, analyze_case, generate_report]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 2
    job_timeout = 600
    max_tries = get_settings().worker_max_tries
    retry_jobs = True
    keep_result = 3600


def run() -> None:
    import sys

    from arq.cli import cli

    sys.argv = ["arq", "apps.worker.main.WorkerSettings"]
    configure_logging()
    cli()
