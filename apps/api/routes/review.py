"""Phase 5: review queue, decisions, and audit trail."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import AuthContext, require_auth
from apps.api.schemas import (
    AuditEventOut,
    ReviewDecisionOut,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    ReviewFlagRequest,
    ReviewTaskOut,
)
from packages.domain.db import get_session
from packages.domain.models import Case, ReviewDecision, ReviewTask
from packages.retrieval.review import (
    apply_review_decision,
    create_user_flag_task,
    list_audit_events,
    list_review_tasks,
    sync_review_tasks_for_case,
)

router = APIRouter(tags=["review"])


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


def _task_out(task: ReviewTask) -> ReviewTaskOut:
    return ReviewTaskOut.model_validate(task)


def _decision_out(decision: ReviewDecision) -> ReviewDecisionOut:
    return ReviewDecisionOut.model_validate(decision)


@router.get("/review-tasks", response_model=list[ReviewTaskOut])
async def list_tenant_review_tasks(
    case_id: uuid.UUID | None = None,
    status: str | None = Query(None, description="OPEN | RESOLVED | CANCELLED | IN_PROGRESS"),
    severity: str | None = Query(None, description="CRITICAL | HIGH | MEDIUM | LOW"),
    task_type: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ReviewTaskOut]:
    if case_id is not None:
        await _assert_case_access(session, case_id, auth.tenant_id)
    tasks = await list_review_tasks(
        session,
        tenant_id=auth.tenant_id,
        case_id=case_id,
        status=status,
        severity=severity,
        task_type=task_type,
        limit=limit,
    )
    return [_task_out(t) for t in tasks]


@router.get("/cases/{case_id}/review-tasks", response_model=list[ReviewTaskOut])
async def list_case_review_tasks(
    case_id: uuid.UUID,
    status: str | None = Query(None),
    severity: str | None = Query(None),
    task_type: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ReviewTaskOut]:
    await _assert_case_access(session, case_id, auth.tenant_id)
    tasks = await list_review_tasks(
        session,
        tenant_id=auth.tenant_id,
        case_id=case_id,
        status=status,
        severity=severity,
        task_type=task_type,
        limit=limit,
    )
    return [_task_out(t) for t in tasks]


@router.get("/cases/{case_id}/review-tasks/{task_id}", response_model=ReviewTaskOut)
async def get_review_task(
    case_id: uuid.UUID,
    task_id: str,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> ReviewTaskOut:
    await _assert_case_access(session, case_id, auth.tenant_id)
    try:
        as_uuid = uuid.UUID(task_id)
        from sqlalchemy import or_

        filt = or_(ReviewTask.id == as_uuid, ReviewTask.task_id == task_id)
    except ValueError:
        from sqlalchemy import or_

        filt = ReviewTask.task_id == task_id
    task = (
        await session.execute(
            select(ReviewTask).where(
                ReviewTask.case_id == case_id,
                ReviewTask.tenant_id == auth.tenant_id,
                filt,
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Review task not found")
    return _task_out(task)


@router.post(
    "/cases/{case_id}/review-tasks/{task_id}/decide",
    response_model=ReviewDecisionResponse,
)
async def decide_review_task(
    case_id: uuid.UUID,
    task_id: str,
    body: ReviewDecisionRequest,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> ReviewDecisionResponse:
    await _assert_case_access(session, case_id, auth.tenant_id)
    actor = auth.actor_label
    try:
        task, decision = await apply_review_decision(
            session,
            case_id=case_id,
            tenant_id=auth.tenant_id,
            task_id=task_id,
            action=body.action,
            actor=actor,
            reason=body.reason,
            note=body.note,
            selected_fact_id=body.selected_fact_id,
            preferred_name=body.preferred_name,
            requested_doc_label=body.requested_doc_label,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ReviewDecisionResponse(task=_task_out(task), decision=_decision_out(decision))


# Guide alias: POST /v1/cases/{id}/review/{task}/decide
@router.post(
    "/cases/{case_id}/review/{task_id}/decide",
    response_model=ReviewDecisionResponse,
    include_in_schema=False,
)
async def decide_review_task_alias(
    case_id: uuid.UUID,
    task_id: str,
    body: ReviewDecisionRequest,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> ReviewDecisionResponse:
    return await decide_review_task(case_id, task_id, body, auth, session)


@router.get(
    "/cases/{case_id}/review-tasks/{task_id}/decisions",
    response_model=list[ReviewDecisionOut],
)
async def list_task_decisions(
    case_id: uuid.UUID,
    task_id: str,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ReviewDecisionOut]:
    await _assert_case_access(session, case_id, auth.tenant_id)
    try:
        as_uuid = uuid.UUID(task_id)
        from sqlalchemy import or_

        filt = or_(ReviewTask.id == as_uuid, ReviewTask.task_id == task_id)
    except ValueError:
        from sqlalchemy import or_

        filt = ReviewTask.task_id == task_id
    task = (
        await session.execute(
            select(ReviewTask)
            .options(selectinload(ReviewTask.decisions))
            .where(
                ReviewTask.case_id == case_id,
                ReviewTask.tenant_id == auth.tenant_id,
                filt,
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Review task not found")
    decisions = sorted(task.decisions or [], key=lambda d: d.created_at, reverse=True)
    return [_decision_out(d) for d in decisions]


@router.post("/cases/{case_id}/review-tasks/flag", response_model=ReviewTaskOut)
async def flag_for_review(
    case_id: uuid.UUID,
    body: ReviewFlagRequest,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> ReviewTaskOut:
    await _assert_case_access(session, case_id, auth.tenant_id)
    actor = auth.actor_label
    task = await create_user_flag_task(
        session,
        case_id=case_id,
        tenant_id=auth.tenant_id,
        summary=body.summary,
        conversation_message_id=body.conversation_message_id,
        fact_id=body.fact_id,
        severity=body.severity,
        actor=actor,
    )
    return _task_out(task)


@router.post("/cases/{case_id}/review-tasks/sync")
async def sync_case_review_tasks(
    case_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _assert_case_access(session, case_id, auth.tenant_id)
    stats = await sync_review_tasks_for_case(
        session, case_id=case_id, tenant_id=auth.tenant_id
    )
    return {"case_id": str(case_id), **stats}


@router.get("/cases/{case_id}/audit-events", response_model=list[AuditEventOut])
async def get_case_audit_events(
    case_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[AuditEventOut]:
    await _assert_case_access(session, case_id, auth.tenant_id)
    events = await list_audit_events(
        session, tenant_id=auth.tenant_id, case_id=case_id, limit=limit
    )
    return [AuditEventOut.model_validate(e) for e in events]
