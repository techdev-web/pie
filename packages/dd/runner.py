"""Orchestrate Phase 6 domain engines and persist findings / risk."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.dd.area import reconcile_areas
from packages.dd.confidence import build_confidence_profiles
from packages.dd.gis import assess_gis_hooks
from packages.dd.legal import build_legal_findings_layer1, enrich_findings_layer2
from packages.dd.ownership import build_ownership_chain
from packages.dd.risk import ENGINE_VERSION as RISK_ENGINE_VERSION
from packages.dd.risk import assess_risk
from packages.dd.shares import account_shares
from packages.dd.survey import analyze_survey_identity
from packages.domain.models import (
    Conflict,
    ConflictFact,
    EvidenceItem,
    Fact,
    FactEvidence,
    GeoFinding,
    LegalFinding,
    MissingEvidence,
    OwnershipEvent,
    Parcel,
    Person,
    RiskSnapshot,
)
from packages.domain.storage import sha256_bytes
from packages.observability import get_logger

log = get_logger("dd.runner")

DD_ENGINE_VERSION = "1.0"


def _public_id(*parts: str) -> str:
    return sha256_bytes("|".join(parts).encode())[:20]


async def run_domain_engines(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    case_fingerprint: str,
) -> dict[str, Any]:
    """Run title/property DD engines and replace persisted Phase 6 artifacts for the case."""
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
                select(Conflict)
                .options(selectinload(Conflict.fact_links).selectinload(ConflictFact.fact))
                .where(Conflict.case_id == case_id, Conflict.tenant_id == tenant_id)
            )
        )
        .scalars()
        .unique()
        .all()
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
    events = list(
        (
            await session.execute(
                select(OwnershipEvent)
                .options(
                    selectinload(OwnershipEvent.parties),
                    selectinload(OwnershipEvent.shares),
                )
                .where(OwnershipEvent.case_id == case_id)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    persons = list(
        (await session.execute(select(Person).where(Person.case_id == case_id))).scalars().all()
    )
    parcels = list(
        (
            await session.execute(
                select(Parcel)
                .options(selectinload(Parcel.identifiers))
                .where(Parcel.case_id == case_id)
            )
        )
        .scalars()
        .unique()
        .all()
    )

    person_names = {str(p.id): p.display_name for p in persons}

    # --- Engines ---
    chain = build_ownership_chain(events, person_names=person_names)
    shares = account_shares(events, person_names=person_names)
    survey_facts = [f for f in facts if f.fact_type == "parcel.survey_number"]
    survey = analyze_survey_identity(parcels, survey_facts=survey_facts)
    area_facts = [f for f in facts if f.fact_type == "parcel.area"]
    areas = reconcile_areas(area_facts)

    survey_match = "UNKNOWN"
    if survey.ambiguities or any(
        f.get("identity_match") == "AMBIGUOUS" for f in survey.findings
    ):
        survey_match = "AMBIGUOUS"
    gis = assess_gis_hooks(survey_identity_match=survey_match)

    layer1 = build_legal_findings_layer1(
        facts=facts,
        conflicts=conflicts,
        gaps=gaps,
        ownership_gaps=chain.gaps,
        share_conflicts=shares.conflicts,
    )
    legal_drafts = await enrich_findings_layer2(layer1)

    geo_drafts: list[dict[str, Any]] = []
    geo_drafts.extend(survey.findings)
    geo_drafts.extend(areas.findings)
    geo_drafts.extend(gis.findings)

    # Evidence links for confidence
    fact_ids = [f.id for f in facts]
    evidence_links: dict[str, list[Any]] = {}
    if fact_ids:
        links = list(
            (
                await session.execute(
                    select(FactEvidence, EvidenceItem)
                    .join(EvidenceItem, EvidenceItem.id == FactEvidence.evidence_item_id)
                    .where(FactEvidence.fact_id.in_(fact_ids))
                )
            ).all()
        )
        fact_id_by_pk = {f.id: f.fact_id for f in facts}
        for fe, ev in links:
            public = fact_id_by_pk.get(fe.fact_id)
            if public:
                evidence_links.setdefault(public, []).append(ev)

    temporal_types: set[str] = set()
    if chain.conflicts:
        temporal_types.add("transaction.date")
        temporal_types.add("party.owner")

    profiles = build_confidence_profiles(
        facts,
        conflicts=conflicts,
        evidence_by_fact=evidence_links,
        temporal_conflict_fact_types=temporal_types,
    )

    risk = assess_risk(
        legal_findings=legal_drafts,
        geo_findings=geo_drafts,
        conflicts=conflicts,
        gaps=gaps,
        share_conflicts=shares.conflicts,
    )

    # --- Persist (replace prior Phase 6 rows for case) ---
    await session.execute(delete(LegalFinding).where(LegalFinding.case_id == case_id))
    await session.execute(delete(GeoFinding).where(GeoFinding.case_id == case_id))
    # Keep historical risk snapshots; insert a new one each run

    persisted_legal = 0
    for draft in legal_drafts:
        fid = f"lf_{_public_id(str(case_id), draft.category, draft.statement[:100], draft.related_conflict_id or '', draft.related_gap_id or '')}"
        session.add(
            LegalFinding(
                id=uuid.uuid4(),
                finding_id=fid,
                tenant_id=tenant_id,
                case_id=case_id,
                category=draft.category,
                severity=draft.severity,
                statement=draft.statement,
                status=draft.status,
                layer=draft.layer,
                evidence_ids=list(draft.evidence_ids),
                related_fact_ids=list(draft.related_fact_ids),
                related_conflict_id=draft.related_conflict_id,
                related_gap_id=draft.related_gap_id,
                missing_evidence=list(draft.missing_evidence),
                recommended_action=draft.recommended_action,
                details=draft.details,
                engine_version=DD_ENGINE_VERSION,
            )
        )
        persisted_legal += 1

    persisted_geo = 0
    for g in geo_drafts:
        gid = f"gf_{_public_id(str(case_id), g.get('finding_type', ''), g.get('statement', '')[:100])}"
        parcel_id = None
        details = g.get("details") or {}
        if details.get("parcel_ids"):
            try:
                parcel_id = uuid.UUID(str(details["parcel_ids"][0]))
            except (ValueError, TypeError, IndexError):
                parcel_id = None
        session.add(
            GeoFinding(
                id=uuid.uuid4(),
                finding_id=gid,
                tenant_id=tenant_id,
                case_id=case_id,
                parcel_id=parcel_id,
                finding_type=g.get("finding_type", "GEO"),
                severity=g.get("severity", "MEDIUM"),
                status=g.get("status", "OPEN"),
                statement=g.get("statement", ""),
                geometry_valid=g.get("geometry_valid"),
                identity_match=g.get("identity_match"),
                boundary_consistent=g.get("boundary_consistent"),
                related_fact_ids=list(g.get("related_fact_ids") or []),
                details=details,
                engine_version=DD_ENGINE_VERSION,
            )
        )
        persisted_geo += 1

    snapshot = RiskSnapshot(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        case_id=case_id,
        case_fingerprint=case_fingerprint,
        risk_level=risk.risk_level,
        score=risk.score,
        weights_version=risk.weights_version,
        drivers=[d.to_dict() for d in risk.drivers],
        ownership_timeline=[e.to_dict() for e in chain.timeline],
        confidence_profiles=[p.to_dict() for p in profiles],
        disclaimer=risk.disclaimer,
        details={
            "share_accounting": shares.to_dict(),
            "survey": survey.to_dict(),
            "area": areas.to_dict(),
            "gis": gis.to_dict(),
            "ownership_gaps": chain.gaps,
            "ownership_conflicts": chain.conflicts,
        },
        engine_version=RISK_ENGINE_VERSION,
    )
    session.add(snapshot)
    await session.commit()

    log.info(
        "domain_engines_done",
        case_id=str(case_id),
        legal_findings=persisted_legal,
        geo_findings=persisted_geo,
        risk_level=risk.risk_level,
        risk_score=risk.score,
        drivers=len(risk.drivers),
    )
    return {
        "legal_findings": persisted_legal,
        "geo_findings": persisted_geo,
        "risk_level": risk.risk_level,
        "risk_score": risk.score,
        "drivers": len(risk.drivers),
        "timeline_events": len(chain.timeline),
        "confidence_profiles": len(profiles),
        "risk_snapshot_id": str(snapshot.id),
    }
