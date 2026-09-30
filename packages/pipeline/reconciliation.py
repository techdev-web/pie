"""Phase 3 cross-document reconciliation — compare only; never invent."""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.domain.models import (
    Case,
    CaseCompletenessSnapshot,
    CaseDocument,
    CaseStageRun,
    Conflict,
    ConflictFact,
    Document,
    DocumentClassification,
    DocumentIntegrityCheck,
    DocumentPage,
    EvidenceItem,
    Fact,
    GraphEdge,
    GraphNode,
    MissingEvidence,
    OwnershipEvent,
    Parcel,
    Person,
)
from packages.domain.storage import sha256_bytes
from packages.domain.textutil import normalize_text
from packages.observability import get_logger

log = get_logger("pipeline.reconciliation")

RECONCILIATION_VERSION = "1.0"
STAGE_NAME = "reconcile"
STAGE_VERSION = "1"

# Fact types that participate in value comparison across documents
COMPARE_GROUPS: dict[str, tuple[str, ...]] = {
    "OWNER_NAME_CONFLICT": ("party.owner", "party.seller", "party.buyer"),
    "SURVEY_NUMBER_CONFLICT": ("parcel.survey_number",),
    "AREA_CONFLICT": ("parcel.area",),
    "DATE_CONFLICT": ("transaction.date",),
    "SHARE_CONFLICT": ("ownership.share",),
    "ENCUMBRANCE_STATUS_CONFLICT": ("encumbrance.mortgage",),
}

AREA_TOLERANCE_SQM = 5.0  # ~0.001 acre

REFERENCE_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(
            r"(?:prior|previous|earlier|old)\s+(?:sale\s+)?deed(?:\s+(?:dated|of|no\.?))?\s*"
            r"(?:(\d{4})|(\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}))?",
            re.I,
        ),
        "sale_deed",
        "Prior sale deed",
    ),
    (
        re.compile(r"sale\s+deed\s+(?:dated\s+)?(?:of\s+)?(\d{4})\b", re.I),
        "sale_deed",
        "Sale deed",
    ),
    (
        re.compile(r"\bmutation\b(?:\s+(?:dated|of|no\.?))?\s*(\d{4})?", re.I),
        "mutation",
        "Mutation",
    ),
    (
        re.compile(
            r"\b(?:encumbrance\s+certificate|e\.?\s*c\.?)\b(?:\s+(?:dated|of|for))?\s*(\d{4})?",
            re.I,
        ),
        "encumbrance_certificate",
        "Encumbrance certificate",
    ),
    (
        re.compile(r"\brelease\s+deed\b", re.I),
        "release_deed",
        "Release deed",
    ),
]


@dataclass
class DetectedConflict:
    conflict_type: str
    severity: str
    summary: str
    fact_ids: list[uuid.UUID]
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class DetectedGap:
    gap_type: str
    referenced_label: str
    required_doc_type: str | None
    summary: str
    referenced_from_document_id: uuid.UUID | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphBuild:
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


def case_fingerprint(doc_hashes: list[str]) -> str:
    joined = "|".join(sorted(doc_hashes))
    return sha256_bytes(joined.encode())


def _norm_value(fact: Fact) -> str | None:
    if fact.value_normalized:
        return fact.value_normalized.strip().lower()
    if fact.value_text:
        return normalize_text(fact.value_text)
    return None


def _values_conflict(fact_type: str, values: set[str]) -> bool:
    if len(values) <= 1:
        return False
    if fact_type == "parcel.area":
        nums: list[float] = []
        for v in values:
            try:
                nums.append(float(v))
            except ValueError:
                return True
        if not nums:
            return False
        return (max(nums) - min(nums)) > AREA_TOLERANCE_SQM
    return True


def detect_fact_conflicts(facts: list[Fact]) -> list[DetectedConflict]:
    """Compare facts across documents; emit OPEN conflicts — never pick a winner."""
    usable = [
        f
        for f in facts
        if f.verification_state
        not in ("NOT_FOUND", "NOT_PROVIDED", "NOT_APPLICABLE", "REQUIRES_REVIEW")
        and _norm_value(f) is not None
    ]

    detected: list[DetectedConflict] = []
    conflicting_fact_ids: set[uuid.UUID] = set()

    for conflict_type, fact_types in COMPARE_GROUPS.items():
        by_key: dict[str, list[Fact]] = defaultdict(list)
        for f in usable:
            if f.fact_type not in fact_types:
                continue
            # Group by fact_type so seller vs buyer don't collide as "owner conflict"
            key = f.fact_type
            by_key[key].append(f)

        for fact_type, group in by_key.items():
            # Need at least two documents contributing
            doc_ids = {f.document_id for f in group if f.document_id}
            if len(doc_ids) < 2 and len(group) < 2:
                continue
            if len({_norm_value(f) for f in group}) <= 1:
                continue

            # Only conflict when distinct docs disagree (same-doc duplicates are noise)
            by_doc: dict[uuid.UUID | None, set[str]] = defaultdict(set)
            for f in group:
                nv = _norm_value(f)
                if nv:
                    by_doc[f.document_id].add(nv)
            if len([d for d in by_doc if d is not None]) < 2:
                # Single document with internal disagreement
                all_vals = set().union(*by_doc.values()) if by_doc else set()
                if not _values_conflict(fact_type, all_vals):
                    continue
            else:
                # Cross-doc: conflict if any doc's values disagree with another's
                representative = {next(iter(vs)) for vs in by_doc.values() if vs}
                if not _values_conflict(fact_type, representative):
                    continue

            values = sorted({_norm_value(f) or "" for f in group})
            summary = (
                f"{conflict_type}: {fact_type} has disagreeing values across documents "
                f"({', '.join(values)}). No winner selected."
            )
            fact_ids = [f.id for f in group]
            conflicting_fact_ids.update(fact_ids)
            detected.append(
                DetectedConflict(
                    conflict_type=conflict_type
                    if conflict_type != "OWNER_NAME_CONFLICT" or fact_type.startswith("party.")
                    else conflict_type,
                    severity="HIGH",
                    summary=summary,
                    fact_ids=fact_ids,
                    details={
                        "fact_type": fact_type,
                        "values": values,
                        "document_ids": [str(d) for d in doc_ids if d],
                    },
                )
            )

    # Ownership sequence: events with dates out of chronological party continuity
    return detected


def detect_ownership_sequence_conflicts(
    events: list[OwnershipEvent],
) -> list[DetectedConflict]:
    dated = [e for e in events if e.event_date is not None]
    if len(dated) < 2:
        return []
    dated_sorted = sorted(dated, key=lambda e: e.event_date or datetime.min.replace(tzinfo=timezone.utc))
    # Flag if two sale events share the same parcel but buyer of earlier != seller of later
    conflicts: list[DetectedConflict] = []
    for i in range(len(dated_sorted) - 1):
        earlier, later = dated_sorted[i], dated_sorted[i + 1]
        if earlier.document_id == later.document_id:
            continue
        if earlier.parcel_id and later.parcel_id and earlier.parcel_id != later.parcel_id:
            continue
        earlier_buyers = {
            p.person_id for p in earlier.parties if p.role in ("buyer", "donee", "owner")
        }
        later_sellers = {
            p.person_id for p in later.parties if p.role in ("seller", "donor")
        }
        if earlier_buyers and later_sellers and earlier_buyers.isdisjoint(later_sellers):
            conflicts.append(
                DetectedConflict(
                    conflict_type="OWNERSHIP_SEQUENCE_CONFLICT",
                    severity="HIGH",
                    summary=(
                        "OWNERSHIP_SEQUENCE_CONFLICT: later transfer sellers do not match "
                        "earlier transfer buyers — chain gap, not resolved automatically."
                    ),
                    fact_ids=[],
                    details={
                        "earlier_event_id": str(earlier.id),
                        "later_event_id": str(later.id),
                        "earlier_date": earlier.event_date.isoformat() if earlier.event_date else None,
                        "later_date": later.event_date.isoformat() if later.event_date else None,
                    },
                )
            )
    return conflicts


def detect_share_conflicts(events: list[OwnershipEvent]) -> list[DetectedConflict]:
    conflicts: list[DetectedConflict] = []
    for event in events:
        shares = event.shares or []
        total = 0.0
        counted = False
        for s in shares:
            if s.share_numerator is not None and s.share_denominator and s.share_denominator != 0:
                total += s.share_numerator / s.share_denominator
                counted = True
        if counted and abs(total - 1.0) > 0.02:
            conflicts.append(
                DetectedConflict(
                    conflict_type="SHARE_CONFLICT",
                    severity="MEDIUM",
                    summary=(
                        f"SHARE_CONFLICT: ownership shares for event {event.id} sum to "
                        f"{total:.4f}, not 1.0."
                    ),
                    fact_ids=[],
                    details={"ownership_event_id": str(event.id), "share_sum": total},
                )
            )
    return conflicts


def _doc_type_for(document: Document, classifications: dict[uuid.UUID, str]) -> str:
    return classifications.get(document.id, "unknown")


def _label_matches_provided(
    label: str,
    required_type: str | None,
    documents: list[Document],
    classifications: dict[uuid.UUID, str],
) -> bool:
    label_n = normalize_text(label)
    year_m = re.search(r"(19|20)\d{2}", label)
    year = year_m.group(0) if year_m else None
    for doc in documents:
        dtype = _doc_type_for(doc, classifications)
        fname = normalize_text(doc.source_filename or "")
        if required_type and dtype == required_type:
            if not year or year in fname or year in dtype:
                return True
            # type matches even without year in filename
            if year and year not in fname:
                # still count as provided if only one of that type
                continue
            return True
        if required_type and required_type.replace("_", " ") in fname:
            return True
        if label_n and label_n[:20] in fname:
            return True
    # Relaxed: any doc of required type counts as provided
    if required_type:
        typed = [d for d in documents if classifications.get(d.id) == required_type]
        if typed and not year:
            return True
        if typed and year:
            for d in typed:
                if year in normalize_text(d.source_filename or ""):
                    return True
    return False


def detect_missing_evidence(
    *,
    facts: list[Fact],
    evidence_items: list[EvidenceItem],
    documents: list[Document],
    classifications: dict[uuid.UUID, str],
) -> list[DetectedGap]:
    gaps: list[DetectedGap] = []
    seen_labels: set[str] = set()

    # Explicit document.reference facts from extraction
    for f in facts:
        if f.fact_type != "document.reference":
            continue
        label = f.value_text or f.value_normalized or "Referenced instrument"
        req = (f.value_json or {}).get("doc_type") if f.value_json else None
        key = normalize_text(label)
        if key in seen_labels:
            continue
        seen_labels.add(key)
        if _label_matches_provided(label, req, documents, classifications):
            continue
        gaps.append(
            DetectedGap(
                gap_type="REFERENCED_INSTRUMENT_MISSING",
                referenced_label=label,
                required_doc_type=req,
                summary=f"Referenced instrument not uploaded: {label}",
                referenced_from_document_id=f.document_id,
                details={"fact_id": f.fact_id},
            )
        )

    # Heuristic scan of evidence text for referenced instruments
    for ev in evidence_items:
        text = ev.text or ""
        for pattern, doc_type, base_label in REFERENCE_PATTERNS:
            m = pattern.search(text)
            if not m:
                continue
            year = next((g for g in m.groups() if g), None)
            # Skip self-reference to "sale deed" when the source doc is itself a sale deed
            src_type = classifications.get(ev.document_id, "unknown")
            if doc_type == src_type and not year and "prior" not in m.group(0).lower() and "previous" not in m.group(0).lower():
                continue
            label = f"{base_label} {year}".strip() if year else base_label
            if doc_type == src_type and year and year in normalize_text(text[:80]):
                # Likely describing this document's own date
                if "prior" not in m.group(0).lower() and "previous" not in m.group(0).lower() and "earlier" not in m.group(0).lower():
                    continue
            key = normalize_text(label)
            if key in seen_labels:
                continue
            seen_labels.add(key)
            if _label_matches_provided(label, doc_type, documents, classifications):
                continue
            gaps.append(
                DetectedGap(
                    gap_type="REFERENCED_INSTRUMENT_MISSING",
                    referenced_label=label,
                    required_doc_type=doc_type,
                    summary=f"Referenced instrument not uploaded: {label}",
                    referenced_from_document_id=ev.document_id,
                    details={"evidence_id": ev.evidence_id, "match": m.group(0)[:120]},
                )
            )

    # Mortgage mentioned without release deed
    mortgage_facts = [
        f
        for f in facts
        if f.fact_type == "encumbrance.mortgage"
        and f.verification_state not in ("NOT_FOUND", "NOT_PROVIDED", "NOT_APPLICABLE")
        and (f.value_text or "").lower() not in ("", "not found", "none", "clear")
    ]
    has_release = any(classifications.get(d.id) == "release_deed" for d in documents)
    if mortgage_facts and not has_release:
        label = "Release deed (mortgage release)"
        key = normalize_text(label)
        if key not in seen_labels:
            seen_labels.add(key)
            gaps.append(
                DetectedGap(
                    gap_type="MORTGAGE_RELEASE_MISSING",
                    referenced_label=label,
                    required_doc_type="release_deed",
                    summary="Mortgage is mentioned but no release deed was uploaded.",
                    referenced_from_document_id=mortgage_facts[0].document_id,
                    details={"mortgage_fact_ids": [f.fact_id for f in mortgage_facts]},
                )
            )

    return gaps


def build_document_graph(
    *,
    documents: list[Document],
    classifications: dict[uuid.UUID, str],
    gaps: list[DetectedGap],
) -> GraphBuild:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    for doc in documents:
        dtype = classifications.get(doc.id, "unknown")
        label = doc.source_filename or f"Document {str(doc.id)[:8]}"
        nodes.append(
            {
                "node_id": f"doc:{doc.id}",
                "node_type": "document",
                "label": label,
                "ref_table": "documents",
                "ref_id": doc.id,
                "status": "PROVIDED",
                "properties": {"doc_type": dtype, "content_hash": doc.content_hash},
            }
        )

    for i, gap in enumerate(gaps):
        if gap.gap_type not in ("REFERENCED_INSTRUMENT_MISSING", "MORTGAGE_RELEASE_MISSING"):
            continue
        missing_nid = f"missing:{sha256_bytes(gap.referenced_label.encode())[:16]}"
        nodes.append(
            {
                "node_id": missing_nid,
                "node_type": "referenced_document",
                "label": gap.referenced_label,
                "ref_table": None,
                "ref_id": None,
                "status": "REFERENCED_BUT_MISSING",
                "properties": {
                    "required_doc_type": gap.required_doc_type,
                    "gap_type": gap.gap_type,
                },
            }
        )
        if gap.referenced_from_document_id:
            edges.append(
                {
                    "edge_id": f"ref:{gap.referenced_from_document_id}:{i}",
                    "from_node_id": f"doc:{gap.referenced_from_document_id}",
                    "to_node_id": missing_nid,
                    "edge_type": "references",
                    "status": "REFERENCED_BUT_MISSING",
                    "properties": {"gap_type": gap.gap_type},
                }
            )

    return GraphBuild(nodes=nodes, edges=edges)


def build_entity_event_graph(
    *,
    persons: list[Person],
    parcels: list[Parcel],
    events: list[OwnershipEvent],
) -> GraphBuild:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    for p in persons:
        nodes.append(
            {
                "node_id": f"person:{p.id}",
                "node_type": "person",
                "label": p.display_name,
                "ref_table": "persons",
                "ref_id": p.id,
                "status": None,
                "properties": {"normalized_name": p.normalized_name},
            }
        )
    for parcel in parcels:
        ids = [pi.normalized_value for pi in (parcel.identifiers or [])]
        nodes.append(
            {
                "node_id": f"parcel:{parcel.id}",
                "node_type": "parcel",
                "label": parcel.display_label,
                "ref_table": "parcels",
                "ref_id": parcel.id,
                "status": None,
                "properties": {"identifiers": ids},
            }
        )
    for event in events:
        label = f"{event.event_type} {event.event_date_raw or ''}".strip()
        nodes.append(
            {
                "node_id": f"event:{event.id}",
                "node_type": "ownership_event",
                "label": label,
                "ref_table": "ownership_events",
                "ref_id": event.id,
                "status": event.verification_state,
                "properties": {
                    "event_type": event.event_type,
                    "registration_number": event.registration_number,
                },
            }
        )
        if event.parcel_id:
            edges.append(
                {
                    "edge_id": f"event_parcel:{event.id}",
                    "from_node_id": f"event:{event.id}",
                    "to_node_id": f"parcel:{event.parcel_id}",
                    "edge_type": "affects_parcel",
                    "status": None,
                    "properties": None,
                }
            )
        for party in event.parties or []:
            edges.append(
                {
                    "edge_id": f"party:{event.id}:{party.person_id}:{party.role}",
                    "from_node_id": f"person:{party.person_id}",
                    "to_node_id": f"event:{event.id}",
                    "edge_type": f"party_{party.role}",
                    "status": None,
                    "properties": {"role": party.role},
                }
            )
    return GraphBuild(nodes=nodes, edges=edges)


def compute_scorecard(
    *,
    documents: list[Document],
    pages: list[DocumentPage],
    integrity: list[DocumentIntegrityCheck],
    evidence_items: list[EvidenceItem],
    facts: list[Fact],
    events: list[OwnershipEvent],
    open_conflicts: int,
    missing_count: int,
    classifications: dict[uuid.UUID, str],
) -> dict[str, str]:
    # File integrity
    if not documents:
        file_integrity = "INCOMPLETE"
    elif any(c.status == "FAIL" for c in integrity):
        file_integrity = "FAIL"
    elif any(c.status == "WARNING" for c in integrity):
        file_integrity = "WARNING"
    else:
        file_integrity = "PASS"

    # Page completeness
    if not pages:
        page_completeness = "INCOMPLETE"
    elif any(c.check_name == "page_sequence" and c.status != "PASS" for c in integrity):
        page_completeness = "WARNING"
    else:
        page_completeness = "PASS"

    # OCR quality
    qualities = [p.quality_label for p in pages if p.quality_label]
    if not evidence_items:
        ocr_quality = "INCOMPLETE"
    elif any(q in ("poor", "blank", "unreadable") for q in qualities):
        ocr_quality = "WARNING"
    else:
        ocr_quality = "GOOD"

    owner_facts = [
        f
        for f in facts
        if f.fact_type in ("party.owner", "party.buyer", "party.seller")
        and f.verification_state not in ("NOT_FOUND", "NOT_PROVIDED")
    ]
    if not owner_facts:
        ownership_evidence = "INCOMPLETE"
    elif open_conflicts > 0:
        ownership_evidence = "PARTIAL"
    else:
        ownership_evidence = "PROVIDED"

    if len(events) == 0:
        transaction_chain = "INCOMPLETE"
    elif missing_count > 0:
        transaction_chain = "PARTIAL"
    elif len(events) == 1:
        transaction_chain = "PARTIAL"
    else:
        transaction_chain = "PROVIDED"

    enc_facts = [f for f in facts if f.fact_type.startswith("encumbrance.")]
    has_release = any(classifications.get(d.id) == "release_deed" for d in documents)
    mortgage_open = any(
        f.fact_type == "encumbrance.mortgage"
        and f.verification_state not in ("NOT_FOUND", "NOT_PROVIDED", "NOT_APPLICABLE")
        for f in enc_facts
    )
    if mortgage_open and not has_release:
        encumbrance_evidence = "INCOMPLETE"
    elif enc_facts:
        encumbrance_evidence = "PROVIDED"
    else:
        encumbrance_evidence = "NOT_PROVIDED"

    return {
        "file_integrity": file_integrity,
        "page_completeness": page_completeness,
        "ocr_quality": ocr_quality,
        "ownership_evidence": ownership_evidence,
        "transaction_chain": transaction_chain,
        "encumbrance_evidence": encumbrance_evidence,
        "gis": "NOT_PROVIDED",
    }


def _public_id(*parts: str) -> str:
    return sha256_bytes("|".join(parts).encode())[:20]


async def run_case_reconciliation(session: AsyncSession, case_id: uuid.UUID) -> dict[str, Any]:
    """Reconcile a case: conflicts, gaps, graphs, scorecard. Idempotent per fingerprint."""
    case = await session.get(Case, case_id)
    if case is None:
        raise ValueError(f"case not found: {case_id}")

    case_docs = list(
        (
            await session.execute(
                select(CaseDocument).where(CaseDocument.case_id == case_id)
            )
        )
        .scalars()
        .all()
    )
    documents = []
    for cd in case_docs:
        doc = await session.get(Document, cd.document_id)
        if doc:
            documents.append(doc)

    fingerprint = case_fingerprint([d.content_hash for d in documents])

    existing = (
        await session.execute(
            select(CaseStageRun).where(
                CaseStageRun.case_id == case_id,
                CaseStageRun.case_fingerprint == fingerprint,
                CaseStageRun.stage_name == STAGE_NAME,
                CaseStageRun.stage_version == STAGE_VERSION,
                CaseStageRun.status == "succeeded",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        log.info(
            "reconcile_skipped",
            case_id=str(case_id),
            fingerprint=fingerprint,
            prior_run=str(existing.id),
        )
        return {"skipped": True, "fingerprint": fingerprint}

    run = CaseStageRun(
        id=uuid.uuid4(),
        tenant_id=case.tenant_id,
        case_id=case_id,
        case_fingerprint=fingerprint,
        stage_name=STAGE_NAME,
        stage_version=STAGE_VERSION,
        status="running",
        skipped=False,
        started_at=datetime.now(timezone.utc),
    )
    session.add(run)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        existing = (
            await session.execute(
                select(CaseStageRun).where(
                    CaseStageRun.case_id == case_id,
                    CaseStageRun.case_fingerprint == fingerprint,
                    CaseStageRun.stage_name == STAGE_NAME,
                    CaseStageRun.stage_version == STAGE_VERSION,
                    CaseStageRun.status == "succeeded",
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"skipped": True, "fingerprint": fingerprint}
        raise

    try:
        result = await _reconcile_body(
            session,
            case=case,
            documents=documents,
            fingerprint=fingerprint,
        )
        run = await session.get(CaseStageRun, run.id)
        if run is not None:
            run.status = "succeeded"
            run.finished_at = datetime.now(timezone.utc)
            await session.commit()
        log.info("reconcile_succeeded", case_id=str(case_id), **{k: v for k, v in result.items() if k != "dimensions"})
        return result
    except Exception as exc:
        await session.rollback()
        run = await session.get(CaseStageRun, run.id)
        if run is not None:
            run.status = "failed"
            run.error_message = str(exc)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            await session.commit()
        log.exception("reconcile_failed", case_id=str(case_id), error=str(exc))
        raise


async def _reconcile_body(
    session: AsyncSession,
    *,
    case: Case,
    documents: list[Document],
    fingerprint: str,
) -> dict[str, Any]:
    case_id = case.id
    tenant_id = case.tenant_id
    doc_ids = [d.id for d in documents]

    classifications_rows = list(
        (
            await session.execute(
                select(DocumentClassification).where(
                    DocumentClassification.document_id.in_(doc_ids) if doc_ids else False
                )
            )
        )
        .scalars()
        .all()
    ) if doc_ids else []
    classifications = {c.document_id: c.doc_type for c in classifications_rows}

    facts = list(
        (
            await session.execute(
                select(Fact).where(Fact.case_id == case_id, Fact.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    evidence_items = list(
        (
            await session.execute(
                select(EvidenceItem).where(EvidenceItem.case_id == case_id)
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
    pages = list(
        (
            await session.execute(
                select(DocumentPage).where(
                    DocumentPage.document_id.in_(doc_ids) if doc_ids else False
                )
            )
        )
        .scalars()
        .all()
    ) if doc_ids else []
    integrity = list(
        (
            await session.execute(
                select(DocumentIntegrityCheck).where(
                    DocumentIntegrityCheck.document_id.in_(doc_ids) if doc_ids else False
                )
            )
        )
        .scalars()
        .all()
    ) if doc_ids else []

    # Clear prior reconciliation artifacts for this case (fresh snapshot)
    old_conflicts = list(
        (
            await session.execute(select(Conflict).where(Conflict.case_id == case_id))
        )
        .scalars()
        .all()
    )
    for c in old_conflicts:
        await session.execute(delete(ConflictFact).where(ConflictFact.conflict_id == c.id))
    await session.execute(delete(Conflict).where(Conflict.case_id == case_id))
    await session.execute(delete(MissingEvidence).where(MissingEvidence.case_id == case_id))
    await session.execute(delete(GraphEdge).where(GraphEdge.case_id == case_id))
    await session.execute(delete(GraphNode).where(GraphNode.case_id == case_id))

    # Reset fact states that reconciliation owns (keep human VERIFIED / UNVERIFIED)
    for f in facts:
        if f.verification_state in ("SUPPORTED", "CORROBORATED", "CONFLICTING"):
            f.verification_state = "EXTRACTED"

    detected = detect_fact_conflicts(facts)
    detected.extend(detect_ownership_sequence_conflicts(events))
    detected.extend(detect_share_conflicts(events))
    gaps = detect_missing_evidence(
        facts=facts,
        evidence_items=evidence_items,
        documents=documents,
        classifications=classifications,
    )

    conflicting_ids: set[uuid.UUID] = set()
    for dc in detected:
        conflicting_ids.update(dc.fact_ids)

    # Persist conflicts
    for dc in detected:
        cid = f"cnf_{_public_id(str(case_id), dc.conflict_type, dc.summary[:80], *[str(x) for x in sorted(dc.fact_ids, key=str)])}"
        conflict = Conflict(
            id=uuid.uuid4(),
            conflict_id=cid,
            tenant_id=tenant_id,
            case_id=case_id,
            conflict_type=dc.conflict_type,
            severity=dc.severity,
            status="OPEN",
            summary=dc.summary,
            details=dc.details,
            reconciliation_version=RECONCILIATION_VERSION,
        )
        session.add(conflict)
        await session.flush()
        for fid in dc.fact_ids:
            session.add(
                ConflictFact(
                    id=uuid.uuid4(),
                    conflict_id=conflict.id,
                    fact_id=fid,
                )
            )

    # Update fact verification states (never downgrade human VERIFIED)
    by_type_docs: dict[str, set[uuid.UUID]] = defaultdict(set)
    by_type_vals: dict[str, set[str]] = defaultdict(set)
    for f in facts:
        if f.id in conflicting_ids:
            if f.verification_state != "VERIFIED":
                f.verification_state = "CONFLICTING"
            continue
        if f.verification_state in (
            "NOT_FOUND",
            "NOT_PROVIDED",
            "NOT_APPLICABLE",
            "AMBIGUOUS",
            "REQUIRES_REVIEW",
            "VERIFIED",
            "UNVERIFIED",
        ):
            continue
        nv = _norm_value(f)
        if not nv or not f.document_id:
            continue
        by_type_docs[f.fact_type].add(f.document_id)
        by_type_vals[f.fact_type].add(nv)

    for f in facts:
        if f.id in conflicting_ids:
            continue
        if f.verification_state != "EXTRACTED":
            continue
        docs_for_type = by_type_docs.get(f.fact_type, set())
        vals = by_type_vals.get(f.fact_type, set())
        if len(docs_for_type) >= 2 and len(vals) == 1:
            f.verification_state = "CORROBORATED"
        elif f.document_id and _norm_value(f):
            f.verification_state = "SUPPORTED"

    # Persist gaps
    for gap in gaps:
        gid = f"gap_{_public_id(str(case_id), gap.gap_type, gap.referenced_label)}"
        session.add(
            MissingEvidence(
                id=uuid.uuid4(),
                gap_id=gid,
                tenant_id=tenant_id,
                case_id=case_id,
                gap_type=gap.gap_type,
                referenced_from_document_id=gap.referenced_from_document_id,
                referenced_label=gap.referenced_label,
                required_doc_type=gap.required_doc_type,
                status="NOT_PROVIDED",
                summary=gap.summary,
                details=gap.details,
                reconciliation_version=RECONCILIATION_VERSION,
            )
        )

    # Graphs
    doc_graph = build_document_graph(
        documents=documents, classifications=classifications, gaps=gaps
    )
    entity_graph = build_entity_event_graph(persons=persons, parcels=parcels, events=events)

    for graph_type, build in (("document", doc_graph), ("entity_event", entity_graph)):
        for n in build.nodes:
            session.add(
                GraphNode(
                    id=uuid.uuid4(),
                    node_id=n["node_id"],
                    tenant_id=tenant_id,
                    case_id=case_id,
                    graph_type=graph_type,
                    node_type=n["node_type"],
                    label=n["label"],
                    ref_table=n.get("ref_table"),
                    ref_id=n.get("ref_id"),
                    status=n.get("status"),
                    properties=n.get("properties"),
                )
            )
        for e in build.edges:
            session.add(
                GraphEdge(
                    id=uuid.uuid4(),
                    edge_id=e["edge_id"],
                    tenant_id=tenant_id,
                    case_id=case_id,
                    graph_type=graph_type,
                    from_node_id=e["from_node_id"],
                    to_node_id=e["to_node_id"],
                    edge_type=e["edge_type"],
                    status=e.get("status"),
                    properties=e.get("properties"),
                )
            )

    open_conflicts = len(detected)
    missing_count = len(gaps)
    dimensions = compute_scorecard(
        documents=documents,
        pages=pages,
        integrity=integrity,
        evidence_items=evidence_items,
        facts=facts,
        events=events,
        open_conflicts=open_conflicts,
        missing_count=missing_count,
        classifications=classifications,
    )
    session.add(
        CaseCompletenessSnapshot(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            case_id=case_id,
            case_fingerprint=fingerprint,
            dimensions=dimensions,
            open_conflicts_count=open_conflicts,
            missing_evidence_count=missing_count,
            reconciliation_version=RECONCILIATION_VERSION,
        )
    )

    await session.commit()

    # Phase 5: re-apply human resolutions that survive conflict rebuild, then sync queue
    review_stats: dict[str, Any] = {}
    try:
        from packages.retrieval.review import (
            reapply_resolved_decisions,
            sync_review_tasks_for_case,
        )

        reapplied = await reapply_resolved_decisions(
            session, case_id=case_id, tenant_id=tenant_id
        )
        review_stats = await sync_review_tasks_for_case(
            session, case_id=case_id, tenant_id=tenant_id
        )
        review_stats["reapplied"] = reapplied

        # Recount open conflicts after resolution re-apply
        open_after = (
            await session.execute(
                select(Conflict).where(
                    Conflict.case_id == case_id,
                    Conflict.status == "OPEN",
                )
            )
        ).scalars().all()
        open_conflicts = len(list(open_after))
        snap = (
            await session.execute(
                select(CaseCompletenessSnapshot)
                .where(CaseCompletenessSnapshot.case_id == case_id)
                .order_by(CaseCompletenessSnapshot.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if snap is not None:
            snap.open_conflicts_count = open_conflicts
            await session.commit()
    except Exception as exc:
        log.warning("review_sync_failed", case_id=str(case_id), error=str(exc))

    # Refresh case memory after reconciliation (Phase 4)
    try:
        from packages.retrieval.memory import refresh_case_memory

        await refresh_case_memory(session, case_id=case_id, tenant_id=tenant_id)
    except Exception as exc:
        log.warning("case_memory_refresh_failed", case_id=str(case_id), error=str(exc))

    return {
        "skipped": False,
        "fingerprint": fingerprint,
        "conflicts": open_conflicts,
        "missing_evidence": missing_count,
        "dimensions": dimensions,
        "review_tasks": review_stats,
        "document_nodes": len(doc_graph.nodes),
        "entity_nodes": len(entity_graph.nodes),
    }
