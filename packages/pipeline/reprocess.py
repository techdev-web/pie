"""Case reprocess: delta or prompt-bump selective jobs without full corpus downtime."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.config import get_settings
from packages.domain.models import (
    AuditEvent,
    Case,
    CaseDocument,
    Document,
    ProcessingJob,
    ProcessingStageRun,
)
from packages.observability import get_logger
from packages.pipeline.versions import AI_STAGES, STAGE_VERSIONS

log = get_logger("pipeline.reprocess")


async def invalidate_stage_runs(
    session: AsyncSession,
    *,
    document_ids: list[uuid.UUID],
    stages: list[str] | None = None,
) -> int:
    """Delete succeeded stage rows so versioned runner will re-execute them."""
    target_stages = stages or list(AI_STAGES)
    result = await session.execute(
        delete(ProcessingStageRun).where(
            ProcessingStageRun.document_id.in_(document_ids),
            ProcessingStageRun.stage_name.in_(target_stages),
            ProcessingStageRun.status == "succeeded",
        )
    )
    return int(result.rowcount or 0)


async def documents_needing_delta(
    session: AsyncSession, case_id: uuid.UUID
) -> list[Document]:
    """Docs missing a succeeded structured_extract (or never processed)."""
    links = (
        await session.execute(
            select(CaseDocument, Document)
            .join(Document, Document.id == CaseDocument.document_id)
            .where(CaseDocument.case_id == case_id, Document.upload_status == "stored")
        )
    ).all()
    stage_v, model_v, prompt_v = STAGE_VERSIONS["structured_extract"]
    needed: list[Document] = []
    for _link, doc in links:
        ok = (
            await session.execute(
                select(ProcessingStageRun.id).where(
                    ProcessingStageRun.document_hash == doc.content_hash,
                    ProcessingStageRun.stage_name == "structured_extract",
                    ProcessingStageRun.stage_version == stage_v,
                    ProcessingStageRun.model_version == model_v,
                    ProcessingStageRun.prompt_version == prompt_v,
                    ProcessingStageRun.status == "succeeded",
                )
            )
        ).scalar_one_or_none()
        if ok is None:
            needed.append(doc)
    return needed


async def reprocess_case(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    mode: str = "delta",
    stages: list[str] | None = None,
    force: bool = False,
    actor: str = "system",
) -> dict[str, Any]:
    """Enqueue processing jobs for affected documents; reconcile after.

    Modes:
    - delta: only docs missing current-version AI stages
    - prompt_bump: all case docs; invalidate selected AI stages (default all AI)
    - force: invalidate selected stages even without version bump
    """
    case = (
        await session.execute(
            select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if case is None:
        raise ValueError("case not found")

    if mode not in ("delta", "prompt_bump"):
        raise ValueError("mode must be delta or prompt_bump")

    target_stages = stages or list(AI_STAGES)
    for s in target_stages:
        if s not in STAGE_VERSIONS:
            raise ValueError(f"unknown stage: {s}")

    if mode == "delta" and not force:
        docs = await documents_needing_delta(session, case_id)
    else:
        rows = (
            await session.execute(
                select(Document)
                .join(CaseDocument, CaseDocument.document_id == Document.id)
                .where(
                    CaseDocument.case_id == case_id,
                    Document.upload_status == "stored",
                )
            )
        ).scalars().all()
        docs = list(rows)

    invalidated = 0
    if mode == "prompt_bump" or force:
        invalidated = await invalidate_stage_runs(
            session,
            document_ids=[d.id for d in docs],
            stages=target_stages,
        )

    job_ids: list[uuid.UUID] = []
    for doc in docs:
        job = ProcessingJob(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            case_id=case_id,
            document_id=doc.id,
            status="queued",
            processing_version=get_settings().processing_version,
        )
        session.add(job)
        job_ids.append(job.id)

    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            case_id=case_id,
            actor=actor,
            action="case.reprocess",
            resource_type="case",
            resource_id=str(case_id),
            details={
                "mode": mode,
                "force": force,
                "stages": target_stages,
                "document_count": len(docs),
                "invalidated_stage_runs": invalidated,
                "job_ids": [str(j) for j in job_ids],
            },
        )
    )
    await session.commit()
    log.info(
        "reprocess_queued",
        case_id=str(case_id),
        mode=mode,
        jobs=len(job_ids),
        invalidated=invalidated,
    )
    return {
        "mode": mode,
        "force": force,
        "stages": target_stages,
        "document_ids": [str(d.id) for d in docs],
        "job_ids": job_ids,
        "invalidated_stage_runs": invalidated,
    }
