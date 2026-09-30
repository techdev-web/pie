from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import AuthContext, require_auth
from apps.api.schemas import (
    CaseCreate,
    CaseDetailOut,
    CaseOut,
    DocumentOut,
    ReprocessRequest,
    ReprocessResponse,
)
from packages.domain.db import get_session
from packages.domain.models import AuditEvent, Case, CaseDocument, CaseMember, Document

router = APIRouter(tags=["cases"])


@router.post("/cases", response_model=CaseOut)
async def create_case(
    body: CaseCreate,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> Case:
    case = Case(
        id=uuid.uuid4(),
        tenant_id=auth.tenant_id,
        title=body.title,
        description=body.description,
        status="open",
    )
    session.add(case)
    session.add(
        CaseMember(
            id=uuid.uuid4(),
            case_id=case.id,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            role="owner",
            label="api-key",
        )
    )
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=auth.tenant_id,
            case_id=case.id,
            actor=auth.actor_label,
            action="case.create",
            resource_type="case",
            resource_id=str(case.id),
            details={"title": body.title},
        )
    )
    await session.commit()
    await session.refresh(case)
    return case


@router.get("/cases", response_model=list[CaseOut])
async def list_cases(
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[Case]:
    result = await session.execute(
        select(Case)
        .where(Case.tenant_id == auth.tenant_id)
        .order_by(Case.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/cases/{case_id}", response_model=CaseDetailOut)
async def get_case(
    case_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> CaseDetailOut:
    result = await session.execute(
        select(Case)
        .where(Case.id == case_id, Case.tenant_id == auth.tenant_id)
        .options(selectinload(Case.case_documents).selectinload(CaseDocument.document))
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    docs = [cd.document for cd in case.case_documents if cd.document is not None]
    return CaseDetailOut(
        id=case.id,
        title=case.title,
        description=case.description,
        status=case.status,
        created_at=case.created_at,
        updated_at=case.updated_at,
        documents=[DocumentOut.model_validate(d) for d in docs],
    )


@router.post("/cases/{case_id}/reprocess", response_model=ReprocessResponse)
async def reprocess_case_endpoint(
    case_id: uuid.UUID,
    body: ReprocessRequest,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> ReprocessResponse:
    from apps.api.queue import enqueue_process_document, enqueue_reconcile_case
    from packages.pipeline.reprocess import reprocess_case

    try:
        result = await reprocess_case(
            session,
            case_id=case_id,
            tenant_id=auth.tenant_id,
            mode=body.mode,
            stages=body.stages,
            force=body.force,
            actor=auth.actor_label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for job_id in result["job_ids"]:
        await enqueue_process_document(job_id)
    if result["job_ids"]:
        await enqueue_reconcile_case(case_id, force=body.mode == "prompt_bump" or body.force)

    return ReprocessResponse(
        mode=result["mode"],
        force=result["force"],
        stages=result["stages"],
        document_ids=result["document_ids"],
        job_ids=result["job_ids"],
        invalidated_stage_runs=result["invalidated_stage_runs"],
        status="queued",
    )
