"""Phase 5: human review queue, decisions, and case-local compounding."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.domain.models import (
    AuditEvent,
    CaseMemory,
    Conflict,
    ConflictFact,
    Fact,
    MemoryWriteback,
    MissingEvidence,
    Person,
    PersonAlias,
    ReviewDecision,
    ReviewTask,
)
from packages.domain.textutil import normalize_text
from packages.observability import get_logger
from packages.retrieval.memory import get_latest_memory, refresh_case_memory

log = get_logger("retrieval.review")

DECISION_ACTIONS = frozenset(
    {"approve", "reject", "merge", "split", "request_docs", "annotate"}
)

# Conflict types that always become review tasks
CONFLICT_TASK_TYPES: dict[str, tuple[str, str]] = {
    # conflict_type -> (task_type, default_severity)
    "OWNER_NAME_CONFLICT": ("OWNER_IDENTITY_AMBIGUITY", "HIGH"),
    "SURVEY_NUMBER_CONFLICT": ("CRITICAL_ID_CONFLICT", "CRITICAL"),
    "AREA_CONFLICT": ("CRITICAL_ID_CONFLICT", "HIGH"),
    "DATE_CONFLICT": ("HIGH_IMPACT_FINDING", "HIGH"),
    "SHARE_CONFLICT": ("SHARE_MATH_INCONSISTENCY", "HIGH"),
    "OWNERSHIP_SEQUENCE_CONFLICT": ("HIGH_IMPACT_FINDING", "HIGH"),
    "ENCUMBRANCE_STATUS_CONFLICT": ("ENCUMBRANCE_UNRESOLVED", "CRITICAL"),
    "OCR_CONFLICT": ("CRITICAL_ID_OCR_CONFLICT", "CRITICAL"),
}

ENCUMBRANCE_GAP_MARKERS = ("MORTGAGE", "ENCUMBRANCE", "CHARGE", "RELEASE")

SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def fingerprint_for_conflict(conflict_type: str, fact_public_ids: list[str]) -> str:
    parts = "|".join([conflict_type, *sorted(fact_public_ids)])
    return "c:" + hashlib.sha256(parts.encode()).hexdigest()[:40]


def fingerprint_for_fact(fact_id: str) -> str:
    return "f:" + hashlib.sha256(fact_id.encode()).hexdigest()[:40]


def fingerprint_for_gap(gap_id: str) -> str:
    return "g:" + hashlib.sha256(gap_id.encode()).hexdigest()[:40]


def fingerprint_for_user_flag(ref: str) -> str:
    return "u:" + hashlib.sha256(ref.encode()).hexdigest()[:40]


def _public_task_id(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]
    return f"rt_{digest}"


def _public_decision_id() -> str:
    return f"rd_{uuid.uuid4().hex[:20]}"


def candidate_from_conflict(conflict: Conflict, fact_public_ids: list[str]) -> dict[str, Any]:
    task_type, severity = CONFLICT_TASK_TYPES.get(
        conflict.conflict_type, ("HIGH_IMPACT_FINDING", conflict.severity or "HIGH")
    )
    return {
        "fingerprint": fingerprint_for_conflict(conflict.conflict_type, fact_public_ids),
        "task_type": task_type,
        "severity": severity if severity in SEVERITY_RANK else "HIGH",
        "title": conflict.conflict_type.replace("_", " ").title(),
        "summary": conflict.summary,
        "source_kind": "conflict",
        "source_ref_id": conflict.conflict_id,
        "related_fact_ids": fact_public_ids,
        "related_conflict_id": conflict.conflict_id,
        "related_gap_id": None,
        "details": {
            "conflict_type": conflict.conflict_type,
            "conflict_severity": conflict.severity,
            "conflict_status": conflict.status,
        },
    }


def candidate_from_fact(fact: Fact) -> dict[str, Any] | None:
    if fact.verification_state == "REQUIRES_REVIEW":
        task_type = "REQUIRES_REVIEW"
        severity = "HIGH"
        if fact.fact_type.startswith("parcel.") or "survey" in (fact.predicate or ""):
            task_type = "CRITICAL_ID_OCR_CONFLICT"
            severity = "CRITICAL"
        elif fact.fact_type.startswith("party."):
            task_type = "OWNER_IDENTITY_AMBIGUITY"
        title = f"Review required: {fact.fact_type}"
    elif fact.verification_state == "AMBIGUOUS" and fact.fact_type.startswith("party."):
        task_type = "OWNER_IDENTITY_AMBIGUITY"
        severity = "HIGH"
        title = f"Ambiguous party: {fact.fact_type}"
    else:
        return None
    return {
        "fingerprint": fingerprint_for_fact(fact.fact_id),
        "task_type": task_type,
        "severity": severity,
        "title": title,
        "summary": (
            f"{fact.predicate}={fact.value_text or '—'} ({fact.verification_state})"
        ),
        "source_kind": "fact",
        "source_ref_id": fact.fact_id,
        "related_fact_ids": [fact.fact_id],
        "related_conflict_id": None,
        "related_gap_id": None,
        "details": {
            "fact_type": fact.fact_type,
            "verification_state": fact.verification_state,
            "value_text": fact.value_text,
        },
    }


def candidate_from_gap(gap: MissingEvidence) -> dict[str, Any] | None:
    gap_type_u = (gap.gap_type or "").upper()
    if not any(m in gap_type_u for m in ENCUMBRANCE_GAP_MARKERS):
        return None
    return {
        "fingerprint": fingerprint_for_gap(gap.gap_id),
        "task_type": "ENCUMBRANCE_UNRESOLVED",
        "severity": "CRITICAL",
        "title": "Unresolved encumbrance / missing release",
        "summary": gap.summary or f"Missing: {gap.referenced_label}",
        "source_kind": "missing_evidence",
        "source_ref_id": gap.gap_id,
        "related_fact_ids": [],
        "related_conflict_id": None,
        "related_gap_id": gap.gap_id,
        "details": {"gap_type": gap.gap_type, "status": gap.status},
    }


async def sync_review_tasks_for_case(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> dict[str, int]:
    """Upsert OPEN review tasks from conflicts, review-needed facts, and encumbrance gaps."""
    conflicts = list(
        (
            await session.execute(
                select(Conflict)
                .options(selectinload(Conflict.fact_links).selectinload(ConflictFact.fact))
                .where(Conflict.case_id == case_id, Conflict.tenant_id == tenant_id)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    facts = list(
        (
            await session.execute(
                select(Fact).where(Fact.case_id == case_id, Fact.tenant_id == tenant_id)
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
                    MissingEvidence.status.in_(("NOT_PROVIDED", "OPEN", "REQUESTED")),
                )
            )
        )
        .scalars()
        .all()
    )

    candidates: list[dict[str, Any]] = []
    for c in conflicts:
        if c.status in ("RESOLVED", "ACKNOWLEDGED"):
            continue
        fact_ids = [
            link.fact.fact_id for link in (c.fact_links or []) if link.fact is not None
        ]
        candidates.append(candidate_from_conflict(c, fact_ids))
    for f in facts:
        cand = candidate_from_fact(f)
        if cand:
            candidates.append(cand)
    for g in gaps:
        if g.status == "REQUESTED":
            continue
        cand = candidate_from_gap(g)
        if cand:
            candidates.append(cand)

    existing = list(
        (
            await session.execute(
                select(ReviewTask).where(
                    ReviewTask.case_id == case_id, ReviewTask.tenant_id == tenant_id
                )
            )
        )
        .scalars()
        .all()
    )
    by_fp = {t.fingerprint: t for t in existing}
    active_fps = {c["fingerprint"] for c in candidates}

    created = updated = stale = 0
    now = datetime.now(timezone.utc)
    field_keys = (
        "task_type",
        "severity",
        "title",
        "summary",
        "source_kind",
        "source_ref_id",
        "fingerprint",
        "related_fact_ids",
        "related_conflict_id",
        "related_gap_id",
        "details",
    )

    for cand in candidates:
        task = by_fp.get(cand["fingerprint"])
        if task is None:
            session.add(
                ReviewTask(
                    id=uuid.uuid4(),
                    task_id=_public_task_id(str(case_id), cand["fingerprint"]),
                    tenant_id=tenant_id,
                    case_id=case_id,
                    status="OPEN",
                    **{k: cand[k] for k in field_keys},
                )
            )
            created += 1
            continue
        if task.status in ("RESOLVED", "CANCELLED"):
            task.source_ref_id = cand["source_ref_id"]
            task.related_conflict_id = cand["related_conflict_id"]
            task.related_gap_id = cand["related_gap_id"]
            task.related_fact_ids = cand["related_fact_ids"]
            task.updated_at = now
            updated += 1
            continue
        for k in field_keys:
            setattr(task, k, cand[k])
        task.status = "OPEN"
        task.updated_at = now
        updated += 1

    for task in existing:
        if task.status != "OPEN":
            continue
        if task.fingerprint in active_fps:
            continue
        if task.source_kind == "user_flag":
            continue
        task.status = "CANCELLED"
        task.resolved_at = now
        task.updated_at = now
        task.details = {**(task.details or {}), "auto_cancelled": "source_cleared"}
        stale += 1

    await session.commit()
    log.info(
        "review_tasks_synced",
        case_id=str(case_id),
        created=created,
        updated=updated,
        stale=stale,
    )
    return {"created": created, "updated": updated, "stale": stale}


async def reapply_resolved_decisions(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> int:
    """After conflict rebuild, re-apply RESOLVED review effects so decisions survive reconcile."""
    tasks = list(
        (
            await session.execute(
                select(ReviewTask)
                .options(selectinload(ReviewTask.decisions))
                .where(
                    ReviewTask.case_id == case_id,
                    ReviewTask.tenant_id == tenant_id,
                    ReviewTask.status == "RESOLVED",
                )
            )
        )
        .scalars()
        .unique()
        .all()
    )
    all_conflicts = list(
        (
            await session.execute(
                select(Conflict)
                .options(selectinload(Conflict.fact_links).selectinload(ConflictFact.fact))
                .where(Conflict.case_id == case_id)
            )
        )
        .scalars()
        .unique()
        .all()
    )

    applied = 0
    for task in tasks:
        if not task.decisions:
            continue
        decision = max(task.decisions, key=lambda d: d.created_at)
        effects = decision.effects or {}

        conflict = None
        cid = task.related_conflict_id or effects.get("conflict_id")
        if cid:
            conflict = next((c for c in all_conflicts if c.conflict_id == cid), None)
        if conflict is None and task.fingerprint.startswith("c:"):
            for c in all_conflicts:
                fids = [
                    link.fact.fact_id
                    for link in (c.fact_links or [])
                    if link.fact is not None
                ]
                if fingerprint_for_conflict(c.conflict_type, fids) == task.fingerprint:
                    conflict = c
                    task.related_conflict_id = c.conflict_id
                    task.source_ref_id = c.conflict_id
                    break
        if conflict is not None:
            conflict.status = "RESOLVED"
            conflict.updated_at = datetime.now(timezone.utc)
            applied += 1

        for public_id, state in (effects.get("fact_states") or {}).items():
            fact = (
                await session.execute(
                    select(Fact).where(Fact.case_id == case_id, Fact.fact_id == public_id)
                )
            ).scalar_one_or_none()
            if fact is not None:
                fact.verification_state = state
                fact.updated_at = datetime.now(timezone.utc)
                applied += 1

    if applied:
        await session.commit()
        log.info("review_resolutions_reapplied", case_id=str(case_id), applied=applied)
    return applied


async def apply_review_decision(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    task_id: str,
    action: str,
    actor: str = "user",
    reason: str | None = None,
    note: str | None = None,
    selected_fact_id: str | None = None,
    preferred_name: str | None = None,
    requested_doc_label: str | None = None,
) -> tuple[ReviewTask, ReviewDecision]:
    action = action.lower().strip()
    if action not in DECISION_ACTIONS:
        raise ValueError(f"unsupported action: {action}")

    task = await _load_task(session, case_id=case_id, tenant_id=tenant_id, task_id=task_id)
    if task.status in ("RESOLVED", "CANCELLED") and action != "annotate":
        raise ValueError(f"task already {task.status}")

    prior_states: dict[str, Any] = {
        "task_status": task.status,
        "facts": {},
        "conflict_status": None,
    }
    effects: dict[str, Any] = {
        "fact_states": {},
        "conflict_id": task.related_conflict_id,
        "aliases_added": [],
        "preferences": {},
        "gaps_updated": [],
    }

    related_facts = await _load_related_facts(session, case_id=case_id, task=task)
    for f in related_facts:
        prior_states["facts"][f.fact_id] = f.verification_state

    conflict = None
    if task.related_conflict_id:
        conflict = (
            await session.execute(
                select(Conflict).where(
                    Conflict.case_id == case_id,
                    Conflict.conflict_id == task.related_conflict_id,
                )
            )
        ).scalar_one_or_none()
        if conflict:
            prior_states["conflict_status"] = conflict.status

    now = datetime.now(timezone.utc)
    writeback_ids: list[uuid.UUID] = []

    if action == "approve":
        winner = selected_fact_id
        if not winner and len(related_facts) == 1:
            winner = related_facts[0].fact_id
        if not winner and related_facts:
            raise ValueError("selected_fact_id required to approve a multi-fact conflict")
        for f in related_facts:
            if winner and f.fact_id == winner:
                f.verification_state = "VERIFIED"
            else:
                f.verification_state = "UNVERIFIED"
            f.updated_at = now
            effects["fact_states"][f.fact_id] = f.verification_state
            writeback_ids.append(
                await _writeback(
                    session,
                    tenant_id=tenant_id,
                    case_id=case_id,
                    fact=f,
                    signal="review_approve",
                    prior=prior_states["facts"].get(f.fact_id),
                    new_state=f.verification_state,
                    note=note,
                    actor=actor,
                )
            )
        if conflict:
            conflict.status = "RESOLVED"
            conflict.updated_at = now
        task.status = "RESOLVED"
        task.resolved_at = now

    elif action == "reject":
        targets = related_facts
        if selected_fact_id:
            targets = [f for f in related_facts if f.fact_id == selected_fact_id] or related_facts
        for f in targets:
            f.verification_state = "UNVERIFIED"
            f.updated_at = now
            effects["fact_states"][f.fact_id] = "UNVERIFIED"
            writeback_ids.append(
                await _writeback(
                    session,
                    tenant_id=tenant_id,
                    case_id=case_id,
                    fact=f,
                    signal="review_reject",
                    prior=prior_states["facts"].get(f.fact_id),
                    new_state="UNVERIFIED",
                    note=note,
                    actor=actor,
                )
            )
        if conflict and len(targets) >= len(related_facts):
            conflict.status = "RESOLVED"
            conflict.updated_at = now
            task.status = "RESOLVED"
            task.resolved_at = now
        elif not conflict:
            task.status = "RESOLVED"
            task.resolved_at = now

    elif action == "merge":
        name = preferred_name
        if not name and selected_fact_id:
            match = next((f for f in related_facts if f.fact_id == selected_fact_id), None)
            name = match.value_text if match else None
        if not name and related_facts:
            name = related_facts[0].value_text
        if not name:
            raise ValueError("preferred_name or selected_fact_id required for merge")
        await _compound_alias(
            session,
            case_id=case_id,
            tenant_id=tenant_id,
            preferred_name=name,
            related_facts=related_facts,
            effects=effects,
        )
        if selected_fact_id:
            for f in related_facts:
                if f.fact_id == selected_fact_id:
                    f.verification_state = "VERIFIED"
                    f.updated_at = now
                    effects["fact_states"][f.fact_id] = "VERIFIED"
                elif f.fact_type.startswith("party."):
                    f.verification_state = "UNVERIFIED"
                    f.updated_at = now
                    effects["fact_states"][f.fact_id] = "UNVERIFIED"
        if conflict:
            conflict.status = "RESOLVED"
            conflict.updated_at = now
        task.status = "RESOLVED"
        task.resolved_at = now

    elif action == "split":
        prefs = await _update_case_preferences(
            session,
            case_id=case_id,
            tenant_id=tenant_id,
            patch={
                "do_not_merge": sorted(
                    {
                        f.value_normalized or f.value_text or f.fact_id
                        for f in related_facts
                        if f.value_normalized or f.value_text or f.fact_id
                    }
                )
            },
        )
        effects["preferences"] = prefs
        task.status = "RESOLVED"
        task.resolved_at = now
        if conflict:
            conflict.status = "ACKNOWLEDGED"
            conflict.updated_at = now

    elif action == "request_docs":
        label = (
            requested_doc_label
            or ((task.details or {}).get("gap_type") if task.related_gap_id else None)
            or task.title
        )
        if task.related_gap_id:
            gap = (
                await session.execute(
                    select(MissingEvidence).where(
                        MissingEvidence.case_id == case_id,
                        MissingEvidence.gap_id == task.related_gap_id,
                    )
                )
            ).scalar_one_or_none()
            if gap:
                gap.status = "REQUESTED"
                gap.updated_at = now
                effects["gaps_updated"].append(gap.gap_id)
        else:
            gap = MissingEvidence(
                id=uuid.uuid4(),
                gap_id=f"gap_req_{uuid.uuid4().hex[:16]}",
                tenant_id=tenant_id,
                case_id=case_id,
                gap_type="REVIEW_REQUESTED_DOC",
                referenced_label=str(label),
                status="REQUESTED",
                summary=note or reason or f"Reviewer requested: {label}",
                details={"from_review_task": task.task_id},
            )
            session.add(gap)
            effects["gaps_updated"].append(gap.gap_id)
            task.related_gap_id = gap.gap_id
        task.status = "RESOLVED"
        task.resolved_at = now

    elif action == "annotate":
        effects["annotation"] = note or reason

    decision = ReviewDecision(
        id=uuid.uuid4(),
        decision_id=_public_decision_id(),
        review_task_id=task.id,
        tenant_id=tenant_id,
        case_id=case_id,
        action=action,
        actor=actor,
        reason=reason,
        note=note,
        selected_fact_id=selected_fact_id,
        prior_states=prior_states,
        effects=effects,
    )
    session.add(decision)
    await session.flush()

    for wb_id in writeback_ids:
        wb = await session.get(MemoryWriteback, wb_id)
        if wb is not None:
            wb.review_decision_id = decision.id

    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            case_id=case_id,
            actor=actor,
            action=f"review.{action}",
            resource_type="review_task",
            resource_id=task.task_id,
            details={
                "decision_id": decision.decision_id,
                "reason": reason,
                "note": note,
                "selected_fact_id": selected_fact_id,
                "prior_states": prior_states,
                "effects": effects,
            },
        )
    )
    task.updated_at = now
    await session.commit()
    await refresh_case_memory(session, case_id=case_id, tenant_id=tenant_id)
    await session.refresh(task)
    await session.refresh(decision)
    log.info(
        "review_decision_applied",
        task_id=task.task_id,
        action=action,
        case_id=str(case_id),
    )
    return task, decision


async def create_user_flag_task(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    summary: str,
    conversation_message_id: str | None = None,
    fact_id: str | None = None,
    severity: str = "MEDIUM",
    actor: str = "user",
) -> ReviewTask:
    ref = conversation_message_id or fact_id or uuid.uuid4().hex
    fp = fingerprint_for_user_flag(f"{case_id}:{ref}:{summary[:80]}")
    existing = (
        await session.execute(
            select(ReviewTask).where(
                ReviewTask.case_id == case_id, ReviewTask.fingerprint == fp
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    task = ReviewTask(
        id=uuid.uuid4(),
        task_id=_public_task_id(str(case_id), fp),
        tenant_id=tenant_id,
        case_id=case_id,
        task_type="USER_FLAG",
        severity=severity if severity in SEVERITY_RANK else "MEDIUM",
        status="OPEN",
        title="User-flagged answer",
        summary=summary,
        source_kind="user_flag",
        source_ref_id=ref,
        fingerprint=fp,
        related_fact_ids=[fact_id] if fact_id else [],
        details={"conversation_message_id": conversation_message_id, "actor": actor},
    )
    session.add(task)
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            case_id=case_id,
            actor=actor,
            action="review.flag",
            resource_type="review_task",
            resource_id=task.task_id,
            details={"summary": summary},
        )
    )
    await session.commit()
    await refresh_case_memory(session, case_id=case_id, tenant_id=tenant_id)
    return task


async def list_review_tasks(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID | None = None,
    status: str | None = None,
    severity: str | None = None,
    task_type: str | None = None,
    limit: int = 100,
) -> list[ReviewTask]:
    q = select(ReviewTask).where(ReviewTask.tenant_id == tenant_id)
    if case_id is not None:
        q = q.where(ReviewTask.case_id == case_id)
    if status:
        q = q.where(ReviewTask.status == status)
    if severity:
        q = q.where(ReviewTask.severity == severity)
    if task_type:
        q = q.where(ReviewTask.task_type == task_type)
    q = q.order_by(ReviewTask.created_at.desc()).limit(min(limit, 500))
    return list((await session.execute(q)).scalars().all())


async def list_audit_events(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    limit: int = 100,
) -> list[AuditEvent]:
    return list(
        (
            await session.execute(
                select(AuditEvent)
                .where(AuditEvent.tenant_id == tenant_id, AuditEvent.case_id == case_id)
                .order_by(AuditEvent.created_at.desc())
                .limit(min(limit, 500))
            )
        )
        .scalars()
        .all()
    )


async def _load_task(
    session: AsyncSession, *, case_id: uuid.UUID, tenant_id: uuid.UUID, task_id: str
) -> ReviewTask:
    try:
        as_uuid = uuid.UUID(task_id)
        filt = or_(ReviewTask.id == as_uuid, ReviewTask.task_id == task_id)
    except ValueError:
        filt = ReviewTask.task_id == task_id
    task = (
        await session.execute(
            select(ReviewTask).where(
                ReviewTask.case_id == case_id,
                ReviewTask.tenant_id == tenant_id,
                filt,
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise LookupError(f"review task not found: {task_id}")
    return task


async def _load_related_facts(
    session: AsyncSession, *, case_id: uuid.UUID, task: ReviewTask
) -> list[Fact]:
    ids = list(task.related_fact_ids or [])
    if not ids:
        return []
    return list(
        (
            await session.execute(
                select(Fact).where(Fact.case_id == case_id, Fact.fact_id.in_(ids))
            )
        )
        .scalars()
        .all()
    )


async def _writeback(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    fact: Fact,
    signal: str,
    prior: str | None,
    new_state: str,
    note: str | None,
    actor: str,
) -> uuid.UUID:
    wb_id = uuid.uuid4()
    session.add(
        MemoryWriteback(
            id=wb_id,
            tenant_id=tenant_id,
            case_id=case_id,
            signal_type=signal,
            fact_id=fact.id,
            prior_state=prior,
            new_state=new_state,
            note=note,
            details={"actor": actor},
        )
    )
    return wb_id


async def _compound_alias(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    preferred_name: str,
    related_facts: list[Fact],
    effects: dict[str, Any],
) -> None:
    """Case-local learning: preferred spelling + person aliases from review merge."""
    norm_preferred = normalize_text(preferred_name)
    prefs = await _update_case_preferences(
        session,
        case_id=case_id,
        tenant_id=tenant_id,
        patch={"preferred_names": {norm_preferred: preferred_name}},
    )
    effects["preferences"] = prefs

    for f in related_facts:
        if f.subject_type == "person" and f.subject_id and f.value_text:
            person = await session.get(Person, f.subject_id)
            if person is None:
                continue
            alias_norm = normalize_text(f.value_text)
            if alias_norm != norm_preferred:
                existing_alias = (
                    await session.execute(
                        select(PersonAlias).where(
                            PersonAlias.person_id == person.id,
                            PersonAlias.normalized_alias == alias_norm,
                        )
                    )
                ).scalar_one_or_none()
                if existing_alias is None:
                    session.add(
                        PersonAlias(
                            id=uuid.uuid4(),
                            person_id=person.id,
                            tenant_id=tenant_id,
                            alias=f.value_text,
                            normalized_alias=alias_norm,
                            source="review",
                        )
                    )
                    effects["aliases_added"].append(
                        {"person_id": str(person.id), "alias": f.value_text}
                    )
            person.display_name = preferred_name
            person.normalized_name = norm_preferred


async def _update_case_preferences(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    patch: dict[str, Any],
) -> dict[str, Any]:
    mem = await get_latest_memory(session, case_id=case_id, tenant_id=tenant_id)
    prefs = dict(mem.user_preferences) if mem and mem.user_preferences else {}
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(prefs.get(k), dict):
            merged = dict(prefs[k])
            merged.update(v)
            prefs[k] = merged
        elif isinstance(v, list) and isinstance(prefs.get(k), list):
            prefs[k] = sorted(set(prefs[k]) | set(v))
        else:
            prefs[k] = v
    if mem is not None:
        mem.user_preferences = prefs
        mem.updated_at = datetime.now(timezone.utc)
    else:
        session.add(
            CaseMemory(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                case_id=case_id,
                version=1,
                key_facts_summary=[],
                open_questions=[],
                user_preferences=prefs,
                summary_text="Initialized by review decision",
            )
        )
    return prefs
