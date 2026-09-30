"""Critical-fact confidence — multi-dimension, not a single opaque float."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CRITICAL_FACT_TYPES = frozenset(
    {
        "party.owner",
        "party.buyer",
        "party.seller",
        "parcel.survey_number",
        "parcel.area",
        "transaction.date",
        "ownership.share",
        "encumbrance.mortgage",
    }
)


@dataclass
class ConfidenceProfile:
    fact_id: str
    fact_type: str
    value_text: str | None
    extraction_confidence: float | None
    evidence_quality: str  # HIGH | MEDIUM | LOW | UNKNOWN
    cross_document_agreement: str  # HIGH | LOW | SINGLE_SOURCE | CONFLICTING
    temporal_consistency: str  # OK | CONFLICT | UNKNOWN
    external_verification: str  # NONE | MATCH | CONFLICT | UNKNOWN
    human_review_status: str  # NONE | PENDING | APPROVED | REJECTED
    verification_state: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "fact_type": self.fact_type,
            "value_text": self.value_text,
            "extraction_confidence": self.extraction_confidence,
            "evidence_quality": self.evidence_quality,
            "cross_document_agreement": self.cross_document_agreement,
            "temporal_consistency": self.temporal_consistency,
            "external_verification": self.external_verification,
            "human_review_status": self.human_review_status,
            "verification_state": self.verification_state,
            "notes": self.notes,
        }


def _evidence_quality(fact: Any, evidence_by_fact: dict[str, list[Any]]) -> str:
    links = evidence_by_fact.get(fact.fact_id, [])
    if not links:
        return "UNKNOWN"
    confs = [
        getattr(ev, "ocr_confidence", None)
        for ev in links
        if getattr(ev, "ocr_confidence", None) is not None
    ]
    if not confs:
        return "MEDIUM" if links else "UNKNOWN"
    avg = sum(confs) / len(confs)
    if avg >= 0.85:
        return "HIGH"
    if avg >= 0.6:
        return "MEDIUM"
    return "LOW"


def build_confidence_profiles(
    facts: list[Any],
    *,
    conflicts: list[Any] | None = None,
    evidence_by_fact: dict[str, list[Any]] | None = None,
    open_review_fact_ids: set[str] | None = None,
    temporal_conflict_fact_types: set[str] | None = None,
) -> list[ConfidenceProfile]:
    """Multi-dimension confidence for critical facts only."""
    evidence_by_fact = evidence_by_fact or {}
    open_review_fact_ids = open_review_fact_ids or set()
    temporal_conflict_fact_types = temporal_conflict_fact_types or set()

    conflicting_fact_ids: set[str] = set()
    for c in conflicts or []:
        if getattr(c, "status", "OPEN") not in ("OPEN", None):
            continue
        for link in getattr(c, "fact_links", None) or []:
            f = getattr(link, "fact", None)
            if f is not None:
                conflicting_fact_ids.add(f.fact_id)

    # Group values by type for agreement
    by_type: dict[str, list[Any]] = {}
    for f in facts:
        if f.fact_type in CRITICAL_FACT_TYPES:
            by_type.setdefault(f.fact_type, []).append(f)

    profiles: list[ConfidenceProfile] = []
    for f in facts:
        if f.fact_type not in CRITICAL_FACT_TYPES:
            continue
        peers = by_type.get(f.fact_type, [])
        norms = {
            (p.value_normalized or p.value_text or "").strip().lower()
            for p in peers
            if p.verification_state
            not in ("NOT_FOUND", "NOT_PROVIDED", "NOT_APPLICABLE")
        }
        norms.discard("")
        docs = {str(p.document_id) for p in peers if p.document_id}

        if f.fact_id in conflicting_fact_ids or f.verification_state == "CONFLICTING":
            agreement = "CONFLICTING"
        elif len(docs) >= 2 and len(norms) == 1:
            agreement = "HIGH"
        elif len(docs) <= 1:
            agreement = "SINGLE_SOURCE"
        else:
            agreement = "LOW"

        if f.fact_type in temporal_conflict_fact_types:
            temporal = "CONFLICT"
        elif f.verification_state == "CONFLICTING" and f.fact_type in (
            "transaction.date",
            "party.owner",
        ):
            temporal = "CONFLICT"
        else:
            temporal = "OK" if f.verification_state in ("SUPPORTED", "CORROBORATED", "VERIFIED") else "UNKNOWN"

        if f.verification_state == "VERIFIED":
            human = "APPROVED"
        elif f.verification_state == "UNVERIFIED":
            human = "REJECTED"
        elif f.fact_id in open_review_fact_ids or f.verification_state == "REQUIRES_REVIEW":
            human = "PENDING"
        else:
            human = "NONE"

        notes: list[str] = []
        if agreement == "CONFLICTING":
            notes.append("Cross-document values disagree.")
        if human == "NONE" and f.fact_type in ("parcel.survey_number", "party.owner"):
            notes.append("Critical identity fact has not been human-reviewed.")

        profiles.append(
            ConfidenceProfile(
                fact_id=f.fact_id,
                fact_type=f.fact_type,
                value_text=f.value_text,
                extraction_confidence=f.confidence,
                evidence_quality=_evidence_quality(f, evidence_by_fact),
                cross_document_agreement=agreement,
                temporal_consistency=temporal,
                external_verification="NONE",  # Phase 6 hooks only; no silent overwrite
                human_review_status=human,
                verification_state=f.verification_state,
                notes=notes,
            )
        )
    return profiles
