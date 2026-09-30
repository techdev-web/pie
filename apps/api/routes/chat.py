"""Phase 4 chat, case memory, and fact writebacks."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import AuthContext, require_auth
from apps.api.schemas import (
    CaseMemoryOut,
    ChatRequest,
    ChatResponse,
    ConversationMessageOut,
    ConversationOut,
    EvidenceCitationOut,
    FactConfirmRequest,
    FactOut,
    FactRejectRequest,
    MemoryWritebackOut,
)
from packages.domain.db import get_session
from packages.domain.models import Case, Conversation, ConversationMessage, MemoryWriteback
from packages.retrieval.chat import answer_case_question
from packages.retrieval.memory import confirm_fact, get_latest_memory, reject_fact

router = APIRouter(tags=["chat"])


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


def _fact_out_minimal(fact) -> FactOut:
    return FactOut(
        id=fact.id,
        fact_id=fact.fact_id,
        case_id=fact.case_id,
        document_id=fact.document_id,
        fact_type=fact.fact_type,
        subject_type=fact.subject_type,
        subject_id=fact.subject_id,
        predicate=fact.predicate,
        value_text=fact.value_text,
        value_normalized=fact.value_normalized,
        value_json=fact.value_json,
        unit=fact.unit,
        verification_state=fact.verification_state,
        confidence=fact.confidence,
        extraction_version=fact.extraction_version,
        created_at=fact.created_at,
        evidence_count=0,
    )


@router.post("/cases/{case_id}/chat", response_model=ChatResponse)
async def chat(
    case_id: uuid.UUID,
    body: ChatRequest,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> ChatResponse:
    await _assert_case_access(session, case_id, auth.tenant_id)
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="message required")
    result = await answer_case_question(
        session,
        case_id=case_id,
        tenant_id=auth.tenant_id,
        message=body.message.strip(),
        conversation_id=body.conversation_id,
    )
    return ChatResponse(
        answer=result["answer"],
        status=result["status"],
        evidence=[EvidenceCitationOut(**e) for e in result.get("evidence") or []],
        conflicts=result.get("conflicts") or [],
        missing_evidence=result.get("missing_evidence") or [],
        open_questions=result.get("open_questions") or [],
        conversation_id=uuid.UUID(result["conversation_id"]),
        message_id=uuid.UUID(result["message_id"]),
        retrieval_trace_id=uuid.UUID(result["retrieval_trace_id"]),
        query_class=result.get("query_class"),
        extracted_ids=result.get("extracted_ids") or [],
        retrieval_paths=result.get("retrieval_paths") or [],
        guardrail_warnings=result.get("guardrail_warnings") or [],
    )


@router.get("/cases/{case_id}/conversations", response_model=list[ConversationOut])
async def list_conversations(
    case_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ConversationOut]:
    await _assert_case_access(session, case_id, auth.tenant_id)
    rows = list(
        (
            await session.execute(
                select(Conversation)
                .where(
                    Conversation.case_id == case_id,
                    Conversation.tenant_id == auth.tenant_id,
                )
                .order_by(Conversation.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [ConversationOut.model_validate(r) for r in rows]


@router.get(
    "/cases/{case_id}/conversations/{conversation_id}/messages",
    response_model=list[ConversationMessageOut],
)
async def list_messages(
    case_id: uuid.UUID,
    conversation_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ConversationMessageOut]:
    await _assert_case_access(session, case_id, auth.tenant_id)
    conv = await session.get(Conversation, conversation_id)
    if conv is None or conv.case_id != case_id or conv.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    rows = list(
        (
            await session.execute(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [ConversationMessageOut.model_validate(r) for r in rows]


@router.get("/cases/{case_id}/memory", response_model=CaseMemoryOut | None)
async def get_memory(
    case_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> CaseMemoryOut | None:
    await _assert_case_access(session, case_id, auth.tenant_id)
    memory = await get_latest_memory(session, case_id=case_id, tenant_id=auth.tenant_id)
    if memory is None:
        return None
    return CaseMemoryOut.model_validate(memory)


@router.get("/cases/{case_id}/memory/writebacks", response_model=list[MemoryWritebackOut])
async def list_writebacks(
    case_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[MemoryWritebackOut]:
    await _assert_case_access(session, case_id, auth.tenant_id)
    rows = list(
        (
            await session.execute(
                select(MemoryWriteback)
                .where(
                    MemoryWriteback.case_id == case_id,
                    MemoryWriteback.tenant_id == auth.tenant_id,
                )
                .order_by(MemoryWriteback.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [MemoryWritebackOut.model_validate(r) for r in rows]


@router.post("/cases/{case_id}/facts/{fact_id}/confirm", response_model=FactOut)
async def confirm_fact_endpoint(
    case_id: uuid.UUID,
    fact_id: str,
    body: FactConfirmRequest | None = None,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> FactOut:
    await _assert_case_access(session, case_id, auth.tenant_id)
    try:
        fact = await confirm_fact(
            session,
            case_id=case_id,
            tenant_id=auth.tenant_id,
            fact_id=fact_id,
            note=(body.note if body else None),
            actor="api",
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Fact not found") from None
    return _fact_out_minimal(fact)


@router.post("/cases/{case_id}/facts/{fact_id}/reject", response_model=FactOut)
async def reject_fact_endpoint(
    case_id: uuid.UUID,
    fact_id: str,
    body: FactRejectRequest | None = None,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> FactOut:
    await _assert_case_access(session, case_id, auth.tenant_id)
    try:
        fact = await reject_fact(
            session,
            case_id=case_id,
            tenant_id=auth.tenant_id,
            fact_id=fact_id,
            note=(body.note if body else None),
            alternate_value=(body.alternate_value if body else None),
            actor="api",
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Fact not found") from None
    return _fact_out_minimal(fact)
