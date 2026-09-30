"""Enqueue document processing jobs on Redis/arq."""

from __future__ import annotations

import uuid

from arq import create_pool
from arq.connections import RedisSettings

from packages.config import get_settings
from packages.observability import get_logger

log = get_logger("queue")


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def enqueue_process_document(job_id: uuid.UUID) -> None:
    redis = await create_pool(_redis_settings())
    try:
        await redis.enqueue_job("process_document", str(job_id))
        log.info("enqueued", job_id=str(job_id))
    finally:
        await redis.aclose()


async def enqueue_reconcile_case(case_id: uuid.UUID, *, force: bool = False) -> None:
    redis = await create_pool(_redis_settings())
    try:
        await redis.enqueue_job("reconcile_case", str(case_id), force)
        log.info("enqueued_reconcile", case_id=str(case_id), force=force)
    finally:
        await redis.aclose()


async def enqueue_analyze_case(
    case_id: uuid.UUID,
    *,
    force: bool = False,
    generate_report: bool = True,
    actor: str = "system",
) -> None:
    redis = await create_pool(_redis_settings())
    try:
        await redis.enqueue_job(
            "analyze_case",
            str(case_id),
            force,
            generate_report,
            actor,
        )
        log.info(
            "enqueued_analyze",
            case_id=str(case_id),
            force=force,
            generate_report=generate_report,
        )
    finally:
        await redis.aclose()


async def enqueue_generate_report(
    case_id: uuid.UUID,
    *,
    actor: str = "system",
    rebuild_reason: str | None = None,
) -> None:
    redis = await create_pool(_redis_settings())
    try:
        await redis.enqueue_job(
            "generate_report",
            str(case_id),
            actor,
            rebuild_reason,
        )
        log.info("enqueued_generate_report", case_id=str(case_id))
    finally:
        await redis.aclose()
