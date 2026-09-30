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


async def enqueue_reconcile_case(case_id: uuid.UUID) -> None:
    redis = await create_pool(_redis_settings())
    try:
        await redis.enqueue_job("reconcile_case", str(case_id))
        log.info("enqueued_reconcile", case_id=str(case_id))
    finally:
        await redis.aclose()
