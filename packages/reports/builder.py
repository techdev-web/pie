"""Generate / regenerate case diligence reports from the verified truth layer."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.domain.models import (
    AuditEvent,
    Case,
    CaseCompletenessSnapshot,
    CaseDocument,
    Conflict,
    ConflictFact,
    Document,
    DocumentClassification,
    Fact,
    LegalFinding,
    MissingEvidence,
    Parcel,
    Person,
    ReportArtifact,
    ReviewDecision,
    ReviewTask,
    RiskSnapshot,
)
from packages.domain.storage import get_storage, sha256_bytes
from packages.observability import get_logger
from packages.reports.pdf import render_report_pdf
from packages.reports.sections import (
    LANGUAGE_RULES,
    REVIEW_AFFECTED_SECTIONS,
    SECTION_KEYS,
    build_conflicts_section,
    build_encumbrance_section,
    build_missing_evidence_section,
    build_ownership_section,
    build_parcel_section,
    build_recommendations_section,
    build_risk_section,
    build_scope_section,
    compute_truth_fingerprint,
    section_fingerprint,
)

log = get_logger("reports")

GENERATOR_VERSION = "1.0"


def _public_report_id(case_id: uuid.UUID, version: int, truth_fp: str) -> str:
    return f"rpt_{sha256_bytes(f'{case_id}:{version}:{truth_fp}'.encode())[:16]}"


def report_storage_key(tenant_id: str, case_id: str, report_id: str, ext: str) -> str:
    return f"tenants/{tenant_id}/cases/{case_id}/reports/{report_id}.{ext}"


async def get_latest_report(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> ReportArtifact | None:
    return (
        await session.execute(
            select(ReportArtifact)
            .where(
                ReportArtifact.case_id == case_id,
                ReportArtifact.tenant_id == tenant_id,
            )
            .order_by(ReportArtifact.version.desc(), ReportArtifact.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def mark_reports_stale(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    reason: str,
) -> int:
    rows = list(
        (
            await session.execute(
                select(ReportArtifact).where(
                    ReportArtifact.case_id == case_id,
                    ReportArtifact.tenant_id == tenant_id,
                    ReportArtifact.status == "READY",
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(timezone.utc)
    for row in rows:
        row.status = "STALE"
        row.rebuild_reason = reason
        row.updated_at = now
    return len(rows)


async def truth_fingerprint_for_case(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> str:
    snapshot = await _load_truth_snapshot(session, case_id=case_id, tenant_id=tenant_id)
    return snapshot["truth_fingerprint"]


async def _load_truth_snapshot(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> dict[str, Any]:
    case = (
        await session.execute(
            select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if case is None:
        raise LookupError("Case not found")

    case_docs = list(
        (
            await session.execute(select(CaseDocument).where(CaseDocument.case_id == case_id))
        )
        .scalars()
        .all()
    )
    documents: list[dict[str, Any]] = []
    classifications: dict[str, str] = {}
    for cd in case_docs:
        doc = await session.get(Document, cd.document_id)
        if doc is None or doc.tenant_id != tenant_id:
            continue
        documents.append(
            {
                "id": str(doc.id),
                "content_hash": doc.content_hash,
                "source_filename": doc.source_filename,
                "page_count": doc.page_count,
                "mime_type": doc.mime_type,
                "upload_status": doc.upload_status,
                "role": cd.role,
            }
        )
        cls = (
            await session.execute(
                select(DocumentClassification)
                .where(DocumentClassification.document_id == doc.id)
                .order_by(DocumentClassification.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if cls:
            classifications[str(doc.id)] = cls.doc_type

    facts = list(
        (
            await session.execute(
                select(Fact).where(Fact.case_id == case_id, Fact.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    fact_dicts = [
        {
            "fact_id": f.fact_id,
            "fact_type": f.fact_type,
            "predicate": f.predicate,
            "value_text": f.value_text,
            "value_normalized": f.value_normalized,
            "verification_state": f.verification_state,
            "unit": f.unit,
            "document_id": str(f.document_id) if f.document_id else None,
        }
        for f in facts
    ]

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
    conflict_dicts = []
    for c in conflicts:
        conflict_dicts.append(
            {
                "conflict_id": c.conflict_id,
                "conflict_type": c.conflict_type,
                "severity": c.severity,
                "status": c.status,
                "summary": c.summary,
                "facts": [
                    {"fact_id": link.fact.fact_id}
                    for link in (c.fact_links or [])
                    if link.fact is not None
                ],
                "fact_ids": [
                    link.fact.fact_id
                    for link in (c.fact_links or [])
                    if link.fact is not None
                ],
            }
        )

    gaps = list(
        (
            await session.execute(
                select(MissingEvidence).where(
                    MissingEvidence.case_id == case_id,
                    MissingEvidence.tenant_id == tenant_id,
                )
            )
        )
        .scalars()
        .all()
    )
    gap_dicts = [
        {
            "gap_id": g.gap_id,
            "gap_type": g.gap_type,
            "referenced_label": g.referenced_label,
            "required_doc_type": g.required_doc_type,
            "status": g.status,
            "summary": g.summary,
        }
        for g in gaps
    ]

    risk_row = (
        await session.execute(
            select(RiskSnapshot)
            .where(RiskSnapshot.case_id == case_id, RiskSnapshot.tenant_id == tenant_id)
            .order_by(RiskSnapshot.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    risk = None
    timeline: list[dict[str, Any]] = []
    if risk_row:
        risk = {
            "risk_level": risk_row.risk_level,
            "score": risk_row.score,
            "weights_version": risk_row.weights_version,
            "drivers": list(risk_row.drivers or []),
            "disclaimer": risk_row.disclaimer,
            "id": str(risk_row.id),
        }
        timeline = list(risk_row.ownership_timeline or [])

    persons = list(
        (
            await session.execute(
                select(Person).where(Person.case_id == case_id, Person.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    person_dicts = [
        {
            "id": str(p.id),
            "display_name": p.display_name,
            "normalized_name": p.normalized_name,
        }
        for p in persons
    ]

    parcels = list(
        (
            await session.execute(
                select(Parcel)
                .options(selectinload(Parcel.identifiers))
                .where(Parcel.case_id == case_id, Parcel.tenant_id == tenant_id)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    parcel_dicts = [
        {
            "id": str(p.id),
            "display_label": p.display_label,
            "identifiers": [
                {
                    "id_type": i.id_type,
                    "id_value": i.id_value,
                    "normalized_value": i.normalized_value,
                }
                for i in (p.identifiers or [])
            ],
        }
        for p in parcels
    ]

    legal = list(
        (
            await session.execute(
                select(LegalFinding).where(
                    LegalFinding.case_id == case_id, LegalFinding.tenant_id == tenant_id
                )
            )
        )
        .scalars()
        .all()
    )
    legal_dicts = [
        {
            "finding_id": f.finding_id,
            "category": f.category,
            "severity": f.severity,
            "status": f.status,
            "statement": f.statement,
            "recommended_action": f.recommended_action,
        }
        for f in legal
    ]

    open_review_count = (
        await session.execute(
            select(func.count())
            .select_from(ReviewTask)
            .where(
                ReviewTask.case_id == case_id,
                ReviewTask.tenant_id == tenant_id,
                ReviewTask.status == "OPEN",
            )
        )
    ).scalar_one()
    decision_count = (
        await session.execute(
            select(func.count())
            .select_from(ReviewDecision)
            .where(
                ReviewDecision.case_id == case_id,
                ReviewDecision.tenant_id == tenant_id,
            )
        )
    ).scalar_one()

    completeness = (
        await session.execute(
            select(CaseCompletenessSnapshot)
            .where(CaseCompletenessSnapshot.case_id == case_id)
            .order_by(CaseCompletenessSnapshot.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    truth_fp = compute_truth_fingerprint(
        case_id=str(case_id),
        documents=documents,
        facts=fact_dicts,
        conflicts=conflict_dicts,
        gaps=gap_dicts,
        risk=risk,
        review_open_count=int(open_review_count or 0),
        decision_count=int(decision_count or 0),
    )

    return {
        "case": case,
        "documents": documents,
        "classifications": classifications,
        "facts": fact_dicts,
        "conflicts": conflict_dicts,
        "gaps": gap_dicts,
        "risk": risk,
        "timeline": timeline,
        "persons": person_dicts,
        "parcels": parcel_dicts,
        "legal_findings": legal_dicts,
        "open_review_count": int(open_review_count or 0),
        "decision_count": int(decision_count or 0),
        "case_fingerprint": completeness.case_fingerprint if completeness else None,
        "truth_fingerprint": truth_fp,
        "open_conflict_statuses": {
            c["conflict_id"]: c["status"] for c in conflict_dicts
        },
    }


def _build_all_sections(snap: dict[str, Any]) -> dict[str, dict[str, Any]]:
    case: Case = snap["case"]
    return {
        "scope": build_scope_section(
            case_title=case.title,
            documents=snap["documents"],
            classifications=snap["classifications"],
        ),
        "parcel_identity": build_parcel_section(
            parcels=snap["parcels"],
            facts=snap["facts"],
            conflicts=snap["conflicts"],
        ),
        "parties_ownership": build_ownership_section(
            persons=snap["persons"],
            timeline=snap["timeline"],
            facts=snap["facts"],
            conflicts=snap["conflicts"],
        ),
        "encumbrances": build_encumbrance_section(
            facts=snap["facts"],
            legal_findings=snap["legal_findings"],
            gaps=snap["gaps"],
        ),
        "conflicts": build_conflicts_section(conflicts=snap["conflicts"]),
        "missing_evidence": build_missing_evidence_section(gaps=snap["gaps"]),
        "risk_drivers": build_risk_section(risk=snap["risk"]),
        "recommended_verifications": build_recommendations_section(
            conflicts=snap["conflicts"],
            gaps=snap["gaps"],
            legal_findings=snap["legal_findings"],
            open_review_count=snap["open_review_count"],
        ),
    }


async def generate_case_report(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    triggered_by: str | None = None,
    rebuild_reason: str | None = None,
    force_sections: set[str] | None = None,
    store_pdf: bool = True,
    actor: str = "system",
) -> ReportArtifact:
    """Build a DD memo from the current truth layer; reuse unchanged sections when possible."""
    snap = await _load_truth_snapshot(session, case_id=case_id, tenant_id=tenant_id)
    case: Case = snap["case"]
    truth_fp = snap["truth_fingerprint"]

    prior = await get_latest_report(session, case_id=case_id, tenant_id=tenant_id)
    if (
        prior is not None
        and prior.status == "READY"
        and prior.truth_fingerprint == truth_fp
        and not force_sections
        and not rebuild_reason
    ):
        log.info(
            "report_reuse_identical",
            case_id=str(case_id),
            report_id=prior.report_id,
            truth_fingerprint=truth_fp,
        )
        return prior

    fresh_sections = _build_all_sections(snap)
    fresh_fps = {k: section_fingerprint(k, v) for k, v in fresh_sections.items()}

    must_rebuild = set(force_sections or [])
    if rebuild_reason and rebuild_reason.startswith("review:"):
        action = rebuild_reason.split(":", 1)[1]
        must_rebuild |= set(REVIEW_AFFECTED_SECTIONS.get(action, SECTION_KEYS))

    sections: dict[str, Any] = {}
    reused: list[str] = []
    prior_body = (prior.body or {}) if prior else {}
    prior_sections = prior_body.get("sections") or {}
    prior_fps = (prior.section_fingerprints or {}) if prior else {}

    for key in SECTION_KEYS:
        prior_sec = prior_sections.get(key)
        prior_fp = prior_fps.get(key)
        if (
            key not in must_rebuild
            and prior_sec is not None
            and prior_fp
            and prior_fp == fresh_fps[key]
        ):
            sections[key] = prior_sec
            reused.append(key)
        else:
            sections[key] = fresh_sections[key]

    version = (prior.version + 1) if prior else 1
    report_id = _public_report_id(case_id, version, truth_fp)
    now = datetime.now(timezone.utc)

    body = {
        "report_type": "dd_memo",
        "case_id": str(case_id),
        "case_title": case.title,
        "generated_at": now.isoformat(),
        "generator_version": GENERATOR_VERSION,
        "truth_fingerprint": truth_fp,
        "case_fingerprint": snap["case_fingerprint"],
        "language_rules": list(LANGUAGE_RULES),
        "conflict_status_index": snap["open_conflict_statuses"],
        "summary": {
            "open_conflicts": sum(
                1 for c in snap["conflicts"] if c.get("status") == "OPEN"
            ),
            "missing_evidence": sum(
                1
                for g in snap["gaps"]
                if g.get("status") in ("NOT_PROVIDED", "OPEN")
            ),
            "risk_level": (snap["risk"] or {}).get("risk_level"),
            "open_review_tasks": snap["open_review_count"],
            "document_count": len(snap["documents"]),
        },
        "sections": sections,
        "section_order": list(SECTION_KEYS),
    }

    if prior and prior.status in ("READY", "STALE"):
        prior.status = "SUPERSEDED"
        prior.updated_at = now

    artifact = ReportArtifact(
        id=uuid.uuid4(),
        report_id=report_id,
        tenant_id=tenant_id,
        case_id=case_id,
        report_type="dd_memo",
        status="READY",
        format="json",
        version=version,
        truth_fingerprint=truth_fp,
        case_fingerprint=snap["case_fingerprint"],
        generator_version=GENERATOR_VERSION,
        title=f"Due Diligence Memo — {case.title}",
        body=body,
        section_fingerprints=fresh_fps,
        reused_sections=reused,
        rebuild_reason=rebuild_reason,
        triggered_by=triggered_by,
        parent_report_id=prior.report_id if prior else None,
        details={
            "rebuilt_sections": [k for k in SECTION_KEYS if k not in reused],
            "open_conflict_count": body["summary"]["open_conflicts"],
        },
        created_at=now,
        updated_at=now,
    )

    if store_pdf:
        try:
            pdf_bytes = render_report_pdf(body, title=artifact.title)
            storage = get_storage()
            key = report_storage_key(str(tenant_id), str(case_id), report_id, "pdf")
            uri = storage.put_bytes(key, pdf_bytes, content_type="application/pdf")
            artifact.pdf_storage_uri = uri
            artifact.format = "json+pdf"
        except Exception as exc:
            log.warning("report_pdf_store_failed", error=str(exc), case_id=str(case_id))

    session.add(artifact)
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            case_id=case_id,
            actor=actor,
            action="report.generated",
            resource_type="report_artifact",
            resource_id=report_id,
            details={
                "version": version,
                "truth_fingerprint": truth_fp,
                "reused_sections": reused,
                "rebuild_reason": rebuild_reason,
                "triggered_by": triggered_by,
            },
        )
    )
    await session.commit()
    await session.refresh(artifact)
    log.info(
        "report_generated",
        case_id=str(case_id),
        report_id=report_id,
        version=version,
        reused=reused,
        rebuild_reason=rebuild_reason,
    )
    return artifact
