"""Facts / structured intelligence API (Phase 2)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import AuthContext, require_auth
from apps.api.schemas import EvidenceOut, FactDetailOut, FactOut
from packages.domain.db import get_session
from packages.domain.models import Case, CaseDocument, EvidenceItem, Fact, FactEvidence

router = APIRouter(tags=["facts"])


async def _assert_case_access(
    session: AsyncSession, case_id: uuid.UUID, tenant_id: uuid.UUID
) -> Case:
    case = (
        await session.execute(
            select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _fact_to_out(fact: Fact, evidence: list[EvidenceItem] | None = None) -> FactOut | FactDetailOut:
    base = {
        "id": fact.id,
        "fact_id": fact.fact_id,
        "case_id": fact.case_id,
        "document_id": fact.document_id,
        "fact_type": fact.fact_type,
        "subject_type": fact.subject_type,
        "subject_id": fact.subject_id,
        "predicate": fact.predicate,
        "value_text": fact.value_text,
        "value_normalized": fact.value_normalized,
        "value_json": fact.value_json,
        "unit": fact.unit,
        "verification_state": fact.verification_state,
        "confidence": fact.confidence,
        "extraction_version": fact.extraction_version,
        "created_at": fact.created_at,
        "evidence_count": len(fact.evidence_links) if fact.evidence_links is not None else 0,
    }
    if evidence is None:
        return FactOut(**base)
    return FactDetailOut(
        **base,
        evidence=[EvidenceOut.model_validate(e) for e in evidence],
    )


@router.get("/cases/{case_id}/facts", response_model=list[FactOut])
async def list_facts(
    case_id: uuid.UUID,
    document_id: uuid.UUID | None = Query(default=None),
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[FactOut]:
    await _assert_case_access(session, case_id, auth.tenant_id)
    q = (
        select(Fact)
        .options(selectinload(Fact.evidence_links))
        .where(Fact.case_id == case_id, Fact.tenant_id == auth.tenant_id)
        .order_by(Fact.fact_type, Fact.created_at)
    )
    if document_id is not None:
        link = (
            await session.execute(
                select(CaseDocument).where(
                    CaseDocument.case_id == case_id,
                    CaseDocument.document_id == document_id,
                    CaseDocument.tenant_id == auth.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if link is None:
            raise HTTPException(status_code=404, detail="Document not in case")
        q = q.where(Fact.document_id == document_id)

    facts = list((await session.execute(q)).scalars().unique().all())
    return [_fact_to_out(f) for f in facts]  # type: ignore[misc]


@router.get("/cases/{case_id}/facts/{fact_id}", response_model=FactDetailOut)
async def get_fact(
    case_id: uuid.UUID,
    fact_id: str,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> FactDetailOut:
    """Why this fact? — returns the fact with cited evidence."""
    await _assert_case_access(session, case_id, auth.tenant_id)

    # Accept either public fact_id string or UUID primary key
    try:
        as_uuid = uuid.UUID(fact_id)
        fact_filter = or_(Fact.id == as_uuid, Fact.fact_id == fact_id)
    except ValueError:
        fact_filter = Fact.fact_id == fact_id

    fact = (
        await session.execute(
            select(Fact)
            .options(selectinload(Fact.evidence_links).selectinload(FactEvidence.evidence_item))
            .where(
                Fact.case_id == case_id,
                Fact.tenant_id == auth.tenant_id,
                fact_filter,
            )
        )
    ).scalar_one_or_none()
    if fact is None:
        raise HTTPException(status_code=404, detail="Fact not found")

    evidence = [link.evidence_item for link in fact.evidence_links if link.evidence_item]
    return _fact_to_out(fact, evidence)  # type: ignore[return-value]
