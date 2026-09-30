"""Case memory refresh and human writebacks (confirm / reject facts)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models import (
    AuditEvent,
    CaseCompletenessSnapshot,
    CaseMemory,
    Conflict,
    Fact,
    MemoryWriteback,
    MissingEvidence,
    ReviewTask,
)
from packages.observability import get_logger

log = get_logger("retrieval.memory")

KEY_FACT_TYPES = (
    "party.owner",
    "party.buyer",
    "party.seller",
    "parcel.survey_number",
    "parcel.area",
    "transaction.date",
    "transaction.registration_number",
    "encumbrance.mortgage",
    "document.reference",
)


async def get_latest_memory(
    session: AsyncSession, *, case_id: uuid.UUID, tenant_id: uuid.UUID
) -> CaseMemory | None:
    return (
        await session.execute(
            select(CaseMemory)
            .where(CaseMemory.case_id == case_id, CaseMemory.tenant_id == tenant_id)
            .order_by(CaseMemory.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def refresh_case_memory(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> CaseMemory:
    """Build a new versioned case memory snapshot from the truth layer."""
    facts = list(
        (
            await session.execute(
                select(Fact).where(Fact.case_id == case_id, Fact.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    conflicts = list(
        (
            await session.execute(
                select(Conflict).where(
                    Conflict.case_id == case_id,
                    Conflict.tenant_id == tenant_id,
                    Conflict.status == "OPEN",
                )
            )
        )
        .scalars()
        .all()
    )
    gaps = list(
        (
            await session.execute(
                select(MissingEvidence).where(
                    MissingEvidence.case_id == case_id,
                    MissingEvidence.tenant_id == tenant_id,
                    MissingEvidence.status.in_(("NOT_PROVIDED", "OPEN")),
                )
            )
        )
        .scalars()
        .all()
    )
    snapshot = (
        await session.execute(
            select(CaseCompletenessSnapshot)
            .where(CaseCompletenessSnapshot.case_id == case_id)
            .order_by(CaseCompletenessSnapshot.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    prior = await get_latest_memory(session, case_id=case_id, tenant_id=tenant_id)
    next_version = (prior.version + 1) if prior else 1
    prefs = dict(prior.user_preferences) if prior and prior.user_preferences else {}

    key_facts: list[dict[str, Any]] = []
    for f in facts:
        if f.fact_type not in KEY_FACT_TYPES and f.verification_state != "VERIFIED":
            continue
        key_facts.append(
            {
                "fact_id": f.fact_id,
                "fact_type": f.fact_type,
                "predicate": f.predicate,
                "value_text": f.value_text,
                "value_normalized": f.value_normalized,
                "verification_state": f.verification_state,
                "document_id": str(f.document_id) if f.document_id else None,
            }
        )
    # Prefer verified first in summary ordering
    state_rank = {
        "VERIFIED": 0,
        "CORROBORATED": 1,
        "SUPPORTED": 2,
        "CONFLICTING": 3,
        "EXTRACTED": 4,
        "REQUIRES_REVIEW": 5,
    }
    key_facts.sort(key=lambda x: (state_rank.get(x["verification_state"], 9), x["fact_type"]))

    open_questions: list[dict[str, Any]] = []
    for c in conflicts:
        open_questions.append(
            {
                "type": "conflict",
                "id": c.conflict_id,
                "question": c.summary,
                "conflict_type": c.conflict_type,
            }
        )
    for g in gaps:
        open_questions.append(
            {
                "type": "missing_evidence",
                "id": g.gap_id,
                "question": f"Upload or locate: {g.referenced_label}",
                "gap_type": g.gap_type,
            }
        )
    review_tasks = list(
        (
            await session.execute(
                select(ReviewTask).where(
                    ReviewTask.case_id == case_id,
                    ReviewTask.tenant_id == tenant_id,
                    ReviewTask.status == "OPEN",
                )
            )
        )
        .scalars()
        .all()
    )
    for rt in review_tasks:
        open_questions.append(
            {
                "type": "review_task",
                "id": rt.task_id,
                "question": rt.summary,
                "task_type": rt.task_type,
                "severity": rt.severity,
            }
        )

    owner_lines = [
        f"{kf['value_text']} ({kf['verification_state']})"
        for kf in key_facts
        if kf["fact_type"] == "party.owner" and kf.get("value_text")
    ]
    survey_lines = [
        f"{kf['value_text']} ({kf['verification_state']})"
        for kf in key_facts
        if kf["fact_type"] == "parcel.survey_number" and kf.get("value_text")
    ]
    summary_parts = [
        f"Case memory v{next_version}.",
        f"Key facts: {len(key_facts)}.",
        f"Open conflicts: {len(conflicts)}.",
        f"Missing evidence: {len(gaps)}.",
    ]
    if owner_lines:
        summary_parts.append("Owners: " + "; ".join(owner_lines[:5]))
    if survey_lines:
        summary_parts.append("Survey/gata: " + "; ".join(survey_lines[:5]))

    memory = CaseMemory(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        case_id=case_id,
        version=next_version,
        key_facts_summary=key_facts,
        open_questions=open_questions,
        user_preferences=prefs,
        last_reconciliation_snapshot_id=snapshot.id if snapshot else None,
        summary_text=" ".join(summary_parts),
    )
    session.add(memory)
    await session.commit()
    log.info("case_memory_refreshed", case_id=str(case_id), version=next_version)
    return memory


async def confirm_fact(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    fact_id: str,
    note: str | None = None,
    actor: str = "user",
) -> Fact:
    fact = await _load_fact(session, case_id=case_id, tenant_id=tenant_id, fact_id=fact_id)
    prior = fact.verification_state
    fact.verification_state = "VERIFIED"
    fact.updated_at = datetime.now(timezone.utc)
    session.add(
        MemoryWriteback(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            case_id=case_id,
            signal_type="confirm",
            fact_id=fact.id,
            prior_state=prior,
            new_state="VERIFIED",
            note=note,
            details={"actor": actor},
        )
    )
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            case_id=case_id,
            actor=actor,
            action="fact.confirm",
            resource_type="fact",
            resource_id=fact.fact_id,
            details={"prior_state": prior, "new_state": "VERIFIED"},
        )
    )
    await session.commit()
    await _close_related_review_tasks(
        session,
        case_id=case_id,
        tenant_id=tenant_id,
        fact_public_id=fact.fact_id,
        actor=actor,
        signal="confirm",
    )
    await refresh_case_memory(session, case_id=case_id, tenant_id=tenant_id)
    return fact


async def reject_fact(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    fact_id: str,
    note: str | None = None,
    alternate_value: str | None = None,
    actor: str = "user",
) -> Fact:
    fact = await _load_fact(session, case_id=case_id, tenant_id=tenant_id, fact_id=fact_id)
    prior = fact.verification_state
    fact.verification_state = "UNVERIFIED"
    fact.updated_at = datetime.now(timezone.utc)
    details: dict[str, Any] = {"actor": actor}
    if alternate_value:
        details["alternate_value"] = alternate_value
    session.add(
        MemoryWriteback(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            case_id=case_id,
            signal_type="reject",
            fact_id=fact.id,
            prior_state=prior,
            new_state="UNVERIFIED",
            note=note,
            details=details,
        )
    )
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            case_id=case_id,
            actor=actor,
            action="fact.reject",
            resource_type="fact",
            resource_id=fact.fact_id,
            details={"prior_state": prior, "new_state": "UNVERIFIED", **details},
        )
    )
    await session.commit()
    await _close_related_review_tasks(
        session,
        case_id=case_id,
        tenant_id=tenant_id,
        fact_public_id=fact.fact_id,
        actor=actor,
        signal="reject",
    )
    await refresh_case_memory(session, case_id=case_id, tenant_id=tenant_id)
    return fact


async def _close_related_review_tasks(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    fact_public_id: str,
    actor: str,
    signal: str,
) -> None:
    """Close OPEN fact-scoped review tasks when confirm/reject shortcuts are used."""
    from packages.domain.models import ReviewDecision, ReviewTask

    tasks = list(
        (
            await session.execute(
                select(ReviewTask).where(
                    ReviewTask.case_id == case_id,
                    ReviewTask.tenant_id == tenant_id,
                    ReviewTask.status == "OPEN",
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(timezone.utc)
    changed = False
    for task in tasks:
        related = list(task.related_fact_ids or [])
        if fact_public_id not in related:
            continue
        if task.source_kind not in ("fact", "conflict") and len(related) > 1:
            continue
        # Single-fact tasks always close; multi-fact conflict tasks only if all resolved externally
        if len(related) > 1 and signal == "confirm":
            continue
        task.status = "RESOLVED"
        task.resolved_at = now
        task.updated_at = now
        decision = ReviewDecision(
            id=uuid.uuid4(),
            decision_id=f"rd_{uuid.uuid4().hex[:20]}",
            review_task_id=task.id,
            tenant_id=tenant_id,
            case_id=case_id,
            action="approve" if signal == "confirm" else "reject",
            actor=actor,
            reason=f"Closed via fact.{signal}",
            selected_fact_id=fact_public_id,
            prior_states={"task_status": "OPEN"},
            effects={
                "fact_states": {
                    fact_public_id: "VERIFIED" if signal == "confirm" else "UNVERIFIED"
                },
                "via": f"fact.{signal}",
            },
        )
        session.add(decision)
        session.add(
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                case_id=case_id,
                actor=actor,
                action=f"review.auto_{signal}",
                resource_type="review_task",
                resource_id=task.task_id,
                details={"fact_id": fact_public_id, "via": f"fact.{signal}"},
            )
        )
        changed = True
    if changed:
        await session.commit()


async def _load_fact(
    session: AsyncSession, *, case_id: uuid.UUID, tenant_id: uuid.UUID, fact_id: str
) -> Fact:
    from sqlalchemy import or_

    try:
        as_uuid = uuid.UUID(fact_id)
        filt = or_(Fact.id == as_uuid, Fact.fact_id == fact_id)
    except ValueError:
        filt = Fact.fact_id == fact_id
    fact = (
        await session.execute(
            select(Fact).where(
                Fact.case_id == case_id, Fact.tenant_id == tenant_id, filt
            )
        )
    ).scalar_one_or_none()
    if fact is None:
        raise LookupError(f"fact not found: {fact_id}")
    return fact
