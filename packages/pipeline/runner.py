"""Idempotent stage runner and pipeline orchestration."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models import Document, ProcessingJob, ProcessingStageRun
from packages.observability import get_logger
from packages.pipeline import stages
from packages.pipeline import extraction as extraction_stage

log = get_logger("pipeline")

StageFn = Callable[[AsyncSession, ProcessingJob, Document], Awaitable[None]]

STAGE_SEQUENCE: list[tuple[str, str, str, str, StageFn]] = [
    # name, stage_version, model_version, prompt_version, fn
    ("integrity", "1", "", "", stages.stage_integrity),
    ("page_split", "1", "", "", stages.stage_page_split),
    ("page_quality", "1", "", "", stages.stage_page_quality),
    ("classify", "1", "mock-or-gemini", "document_classify:1", stages.stage_classify),
    ("ocr", "1", "mock-or-gemini", "page_ocr:1", stages.stage_ocr),
    ("evidence_persist", "1", "", "", stages.stage_evidence_persist),
    (
        "structured_extract",
        "1",
        "mock-or-gemini",
        "structured_extract:1",
        extraction_stage.stage_structured_extract,
    ),
]


async def _already_succeeded(
    session: AsyncSession,
    *,
    document_hash: str,
    stage_name: str,
    stage_version: str,
    model_version: str,
    prompt_version: str,
) -> ProcessingStageRun | None:
    q = await session.execute(
        select(ProcessingStageRun).where(
            ProcessingStageRun.document_hash == document_hash,
            ProcessingStageRun.stage_name == stage_name,
            ProcessingStageRun.stage_version == stage_version,
            ProcessingStageRun.model_version == model_version,
            ProcessingStageRun.prompt_version == prompt_version,
            ProcessingStageRun.status == "succeeded",
        )
    )
    return q.scalar_one_or_none()


async def run_stage(
    session: AsyncSession,
    job: ProcessingJob,
    document: Document,
    stage_name: str,
    stage_version: str,
    model_version: str,
    prompt_version: str,
    fn: StageFn,
) -> None:
    existing = await _already_succeeded(
        session,
        document_hash=document.content_hash,
        stage_name=stage_name,
        stage_version=stage_version,
        model_version=model_version,
        prompt_version=prompt_version,
    )
    if existing is not None:
        log.info(
            "stage_skipped",
            stage=stage_name,
            document_id=str(document.id),
            prior_run=str(existing.id),
        )
        return

    run = ProcessingStageRun(
        id=uuid.uuid4(),
        job_id=job.id,
        tenant_id=job.tenant_id,
        document_id=document.id,
        document_hash=document.content_hash,
        stage_name=stage_name,
        stage_version=stage_version,
        model_version=model_version,
        prompt_version=prompt_version,
        status="running",
        skipped=False,
        started_at=datetime.now(timezone.utc),
    )
    session.add(run)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        # Concurrent job may have succeeded meanwhile
        existing = await _already_succeeded(
            session,
            document_hash=document.content_hash,
            stage_name=stage_name,
            stage_version=stage_version,
            model_version=model_version,
            prompt_version=prompt_version,
        )
        if existing is not None:
            log.info("stage_skipped_race", stage=stage_name, document_id=str(document.id))
            return
        raise

    try:
        await fn(session, job, document)
        # refresh run in case session state changed
        run = await session.get(ProcessingStageRun, run.id)
        if run is None:
            return
        run.status = "succeeded"
        run.finished_at = datetime.now(timezone.utc)
        await session.commit()
        log.info("stage_succeeded", stage=stage_name, document_id=str(document.id))
    except Exception as exc:
        await session.rollback()
        run = await session.get(ProcessingStageRun, run.id)
        if run is not None:
            run.status = "failed"
            run.error_message = str(exc)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            await session.commit()
        log.exception("stage_failed", stage=stage_name, error=str(exc))
        raise


async def run_document_pipeline(session: AsyncSession, job_id: uuid.UUID) -> None:
    job = await session.get(ProcessingJob, job_id)
    if job is None:
        raise ValueError(f"job not found: {job_id}")

    document = await session.get(Document, job.document_id)
    if document is None:
        raise ValueError(f"document not found: {job.document_id}")

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    await session.commit()

    try:
        for name, stage_version, model_version, prompt_version, fn in STAGE_SEQUENCE:
            await run_stage(
                session,
                job,
                document,
                name,
                stage_version,
                model_version,
                prompt_version,
                fn,
            )
        job.status = "succeeded"
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()
        log.info("job_succeeded", job_id=str(job_id))
    except Exception as exc:
        job.status = "failed"
        job.error_message = str(exc)[:2000]
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()
        raise
