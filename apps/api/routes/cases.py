from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import AuthContext, require_auth
from apps.api.schemas import CaseCreate, CaseDetailOut, CaseOut, DocumentOut
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
            role="owner",
            label="api-key",
        )
    )
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=auth.tenant_id,
            case_id=case.id,
            actor=str(auth.api_key_id),
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
