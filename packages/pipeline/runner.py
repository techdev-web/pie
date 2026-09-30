"""Idempotent stage runner and pipeline orchestration."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.config import get_settings
from packages.domain.models import Document, ProcessingJob, ProcessingStageRun
from packages.observability import bind_pipeline_context, get_logger, pipeline_context
from packages.pipeline import extraction as extraction_stage
from packages.pipeline import indexing as indexing_stage
from packages.pipeline import stages
from packages.pipeline.versions import EVIDENCE_COMPLETE_STAGE, STAGE_VERSIONS

log = get_logger("pipeline")

StageFn = Callable[[AsyncSession, ProcessingJob, Document], Awaitable[None]]

_STAGE_FNS: dict[str, StageFn] = {
    "integrity": stages.stage_integrity,
    "page_split": stages.stage_page_split,
    "page_quality": stages.stage_page_quality,
    "classify": stages.stage_classify,
    "ocr": stages.stage_ocr,
    "evidence_persist": stages.stage_evidence_persist,
    "structured_extract": extraction_stage.stage_structured_extract,
    "index_embeddings": indexing_stage.stage_index_embeddings,
}

# Built from centralized versions so prompt bumps re-run selectively
STAGE_SEQUENCE: list[tuple[str, str, str, str, StageFn]] = [
    (name, *STAGE_VERSIONS[name], _STAGE_FNS[name])
    for name in (
        "integrity",
        "page_split",
        "page_quality",
        "classify",
        "ocr",
        "evidence_persist",
        "structured_extract",
        "index_embeddings",
    )
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

    timeout = get_settings().stage_timeout_seconds
    bind_pipeline_context(
        tenant_id=str(job.tenant_id),
        case_id=str(job.case_id) if job.case_id else None,
        document_id=str(document.id),
        job_id=str(job.id),
        stage=stage_name,
    )
    try:
        await asyncio.wait_for(fn(session, job, document), timeout=timeout)
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
            msg = str(exc)
            if isinstance(exc, asyncio.TimeoutError):
                msg = f"stage timeout after {timeout}s"
            run.error_message = msg[:2000]
            run.finished_at = datetime.now(timezone.utc)
            await session.commit()
        log.exception("stage_failed", stage=stage_name, error=str(exc))
        raise


def _evidence_completed(succeeded_stages: set[str]) -> bool:
    return EVIDENCE_COMPLETE_STAGE in succeeded_stages


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

    succeeded: set[str] = set()
    with pipeline_context(
        tenant_id=str(job.tenant_id),
        case_id=str(job.case_id) if job.case_id else None,
        document_id=str(document.id),
        job_id=str(job_id),
    ):
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
                succeeded.add(name)
            job.status = "succeeded"
            job.finished_at = datetime.now(timezone.utc)
            await session.commit()
            log.info("job_succeeded", job_id=str(job_id))
        except Exception as exc:
            # Partial success: keep evidence even if later AI stages fail
            if _evidence_completed(succeeded):
                job.status = "partial"
                job.error_message = f"partial after evidence: {exc}"[:2000]
                job.finished_at = datetime.now(timezone.utc)
                await session.commit()
                log.warning(
                    "job_partial",
                    job_id=str(job_id),
                    succeeded=sorted(succeeded),
                    error=str(exc),
                )
                return
            job.status = "failed"
            job.error_message = str(exc)[:2000]
            job.finished_at = datetime.now(timezone.utc)
            await session.commit()
            raise


async def run_document_pipeline_with_reconcile(
    session: AsyncSession, job_id: uuid.UUID
) -> uuid.UUID | None:
    """Run document stages; return case_id so the worker can enqueue reconciliation."""
    job = await session.get(ProcessingJob, job_id)
    case_id = job.case_id if job else None
    await run_document_pipeline(session, job_id)
    job = await session.get(ProcessingJob, job_id)
    if job and job.status in ("succeeded", "partial"):
        return case_id
    return None
