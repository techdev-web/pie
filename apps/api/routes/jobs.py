from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import AuthContext, require_auth
from apps.api.schemas import JobOut
from packages.domain.db import get_session
from packages.domain.models import ProcessingJob

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(
    job_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> JobOut:
    result = await session.execute(
        select(ProcessingJob)
        .where(ProcessingJob.id == job_id, ProcessingJob.tenant_id == auth.tenant_id)
        .options(selectinload(ProcessingJob.stage_runs))
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    stages = [
        {
            "stage_name": s.stage_name,
            "stage_version": s.stage_version,
            "status": s.status,
            "skipped": s.skipped,
            "error_message": s.error_message,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "finished_at": s.finished_at.isoformat() if s.finished_at else None,
        }
        for s in sorted(job.stage_runs, key=lambda x: x.created_at or x.stage_name)
    ]
    return JobOut(
        id=job.id,
        case_id=job.case_id,
        document_id=job.document_id,
        status=job.status,
        processing_version=job.processing_version,
        error_message=job.error_message,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        stages=stages,
    )
