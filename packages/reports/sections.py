"""Section builders and fingerprint helpers for DD / TSR reports."""

from __future__ import annotations

import hashlib
import json
from typing import Any

SECTION_KEYS = (
    "scope",
    "parcel_identity",
    "parties_ownership",
    "encumbrances",
    "conflicts",
    "missing_evidence",
    "risk_drivers",
    "recommended_verifications",
)

# After a review action, only these sections must be rebuilt (others may reuse).
REVIEW_AFFECTED_SECTIONS: dict[str, tuple[str, ...]] = {
    "approve": (
        "conflicts",
        "parties_ownership",
        "encumbrances",
        "risk_drivers",
        "recommended_verifications",
    ),
    "reject": (
        "conflicts",
        "parties_ownership",
        "risk_drivers",
        "recommended_verifications",
    ),
    "merge": (
        "parties_ownership",
        "conflicts",
        "recommended_verifications",
    ),
    "split": (
        "parties_ownership",
        "conflicts",
        "recommended_verifications",
    ),
    "request_docs": (
        "missing_evidence",
        "recommended_verifications",
    ),
    "annotate": ("recommended_verifications",),
}

LANGUAGE_RULES = (
    "Evidence-first: material claims cite verification state and document provenance.",
    "No false certainty: OPEN conflicts and NOT_PROVIDED gaps are reported as unresolved.",
    "Distinguish document claims from verified conclusions.",
    "Risk score is a weighted diligence checklist, not a legal title conclusion.",
)


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def section_fingerprint(section_key: str, payload: Any) -> str:
    return _stable_hash({"section": section_key, "payload": payload})


def compute_truth_fingerprint(
    *,
    case_id: str,
    documents: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    risk: dict[str, Any] | None,
    review_open_count: int,
    decision_count: int,
) -> str:
    """Fingerprint of the truth layer shared by chat and reports."""
    return _stable_hash(
        {
            "case_id": case_id,
            "documents": sorted(
                (
                    {
                        "id": d.get("id"),
                        "hash": d.get("content_hash"),
                        "filename": d.get("source_filename"),
                    }
                    for d in documents
                ),
                key=lambda x: str(x.get("id") or ""),
            ),
            "facts": sorted(
                (
                    {
                        "fact_id": f.get("fact_id"),
                        "type": f.get("fact_type"),
                        "state": f.get("verification_state"),
                        "value": f.get("value_normalized") or f.get("value_text"),
                    }
                    for f in facts
                ),
                key=lambda x: str(x.get("fact_id") or ""),
            ),
            "conflicts": sorted(
                (
                    {
                        "conflict_id": c.get("conflict_id"),
                        "type": c.get("conflict_type"),
                        "status": c.get("status"),
                        "summary": c.get("summary"),
                    }
                    for c in conflicts
                ),
                key=lambda x: str(x.get("conflict_id") or ""),
            ),
            "gaps": sorted(
                (
                    {
                        "gap_id": g.get("gap_id"),
                        "type": g.get("gap_type"),
                        "status": g.get("status"),
                        "label": g.get("referenced_label"),
                    }
                    for g in gaps
                ),
                key=lambda x: str(x.get("gap_id") or ""),
            ),
            "risk": {
                "level": (risk or {}).get("risk_level"),
                "score": (risk or {}).get("score"),
                "drivers": (risk or {}).get("drivers") or [],
            },
            "review_open_count": review_open_count,
            "decision_count": decision_count,
        }
    )


def build_scope_section(
    *,
    case_title: str,
    documents: list[dict[str, Any]],
    classifications: dict[str, str],
) -> dict[str, Any]:
    docs = []
    for d in documents:
        docs.append(
            {
                "document_id": d.get("id"),
                "filename": d.get("source_filename"),
                "content_hash": d.get("content_hash"),
                "page_count": d.get("page_count"),
                "mime_type": d.get("mime_type"),
                "role": d.get("role"),
                "doc_type": classifications.get(str(d.get("id"))),
                "upload_status": d.get("upload_status"),
            }
        )
    return {
        "heading": "Scope of documents reviewed",
        "case_title": case_title,
        "document_count": len(docs),
        "documents": docs,
        "notes": [
            "Scope is limited to documents uploaded to this case.",
            "Absence from this list is not proof that an instrument does not exist.",
        ],
    }


def build_parcel_section(
    *,
    parcels: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    survey_facts = [
        f
        for f in facts
        if f.get("fact_type", "").startswith("parcel.")
        or f.get("predicate") in ("survey_number", "khata", "gata", "area")
    ]
    survey_conflicts = [
        c
        for c in conflicts
        if c.get("conflict_type") in ("SURVEY_NUMBER_CONFLICT", "AREA_CONFLICT")
        and c.get("status") == "OPEN"
    ]
    return {
        "heading": "Parcel / property identity",
        "parcels": parcels,
        "identity_facts": [
            {
                "fact_id": f.get("fact_id"),
                "fact_type": f.get("fact_type"),
                "predicate": f.get("predicate"),
                "value": f.get("value_text"),
                "normalized": f.get("value_normalized"),
                "verification_state": f.get("verification_state"),
                "unit": f.get("unit"),
            }
            for f in survey_facts
        ],
        "open_identity_conflicts": [
            {
                "conflict_id": c.get("conflict_id"),
                "conflict_type": c.get("conflict_type"),
                "status": c.get("status"),
                "summary": c.get("summary"),
            }
            for c in survey_conflicts
        ],
        "conclusion": (
            "Parcel identity remains unresolved due to open survey/area conflicts."
            if survey_conflicts
            else "Parcel identity statements below reflect uploaded evidence only."
        ),
    }


def build_ownership_section(
    *,
    persons: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    party_facts = [
        f
        for f in facts
        if f.get("fact_type", "").startswith("party.")
        or f.get("predicate") in ("owner_name", "buyer", "seller", "donor", "donee")
    ]
    owner_conflicts = [
        c
        for c in conflicts
        if c.get("conflict_type")
        in ("OWNER_NAME_CONFLICT", "OWNERSHIP_SEQUENCE_CONFLICT", "SHARE_CONFLICT", "DATE_CONFLICT")
        and c.get("status") == "OPEN"
    ]
    verified = [f for f in party_facts if f.get("verification_state") in ("VERIFIED", "CORROBORATED")]
    return {
        "heading": "Parties & ownership chain",
        "persons": persons,
        "ownership_timeline": timeline,
        "party_facts": [
            {
                "fact_id": f.get("fact_id"),
                "predicate": f.get("predicate"),
                "value": f.get("value_text"),
                "verification_state": f.get("verification_state"),
            }
            for f in party_facts
        ],
        "verified_party_facts": [
            {
                "fact_id": f.get("fact_id"),
                "predicate": f.get("predicate"),
                "value": f.get("value_text"),
                "verification_state": f.get("verification_state"),
            }
            for f in verified
        ],
        "open_ownership_conflicts": [
            {
                "conflict_id": c.get("conflict_id"),
                "conflict_type": c.get("conflict_type"),
                "status": c.get("status"),
                "summary": c.get("summary"),
            }
            for c in owner_conflicts
        ],
        "conclusion": (
            "Ownership chain has open conflicts; do not treat a single name as conclusive."
            if owner_conflicts
            else "Ownership statements distinguish EXTRACTED claims from VERIFIED conclusions."
        ),
    }


def build_encumbrance_section(
    *,
    facts: list[dict[str, Any]],
    legal_findings: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    enc_facts = [
        f
        for f in facts
        if "encumbrance" in (f.get("fact_type") or "")
        or f.get("predicate") in ("mortgage", "charge", "lien", "easement")
    ]
    enc_gaps = [
        g
        for g in gaps
        if g.get("status") in ("NOT_PROVIDED", "OPEN")
        and (
            "MORTGAGE" in (g.get("gap_type") or "")
            or "RELEASE" in (g.get("gap_type") or "")
            or "ENCUMBRANCE" in (g.get("gap_type") or "")
        )
    ]
    unresolved_legal = [
        f for f in legal_findings if f.get("status") in ("UNRESOLVED", "OPEN", "REQUIRES_REVIEW")
    ]
    clear = not enc_facts and not enc_gaps and not unresolved_legal
    return {
        "heading": "Encumbrances & charges",
        "encumbrance_facts": [
            {
                "fact_id": f.get("fact_id"),
                "fact_type": f.get("fact_type"),
                "value": f.get("value_text"),
                "verification_state": f.get("verification_state"),
            }
            for f in enc_facts
        ],
        "legal_findings": [
            {
                "finding_id": f.get("finding_id"),
                "category": f.get("category"),
                "status": f.get("status"),
                "statement": f.get("statement"),
                "recommended_action": f.get("recommended_action"),
            }
            for f in unresolved_legal
        ],
        "missing_release_or_charge_docs": [
            {
                "gap_id": g.get("gap_id"),
                "gap_type": g.get("gap_type"),
                "status": g.get("status"),
                "summary": g.get("summary"),
            }
            for g in enc_gaps
        ],
        "conclusion": (
            "No encumbrance signals in the uploaded set — this is NOT a title-clear certificate."
            if clear
            else "Encumbrance status is UNRESOLVED where mortgages/charges lack releases or remain open."
        ),
    }


def build_conflicts_section(*, conflicts: list[dict[str, Any]]) -> dict[str, Any]:
    open_rows = [c for c in conflicts if c.get("status") == "OPEN"]
    resolved = [c for c in conflicts if c.get("status") == "RESOLVED"]
    return {
        "heading": "Conflicts & unresolved items",
        "open_count": len(open_rows),
        "resolved_count": len(resolved),
        "open_conflicts": [
            {
                "conflict_id": c.get("conflict_id"),
                "conflict_type": c.get("conflict_type"),
                "severity": c.get("severity"),
                "status": c.get("status"),
                "summary": c.get("summary"),
                "fact_ids": [
                    (link.get("fact_id") if isinstance(link, dict) else None)
                    for link in (c.get("facts") or c.get("fact_ids") or [])
                ],
            }
            for c in open_rows
        ],
        "resolved_conflicts": [
            {
                "conflict_id": c.get("conflict_id"),
                "conflict_type": c.get("conflict_type"),
                "status": c.get("status"),
                "summary": c.get("summary"),
            }
            for c in resolved
        ],
        "conclusion": (
            f"{len(open_rows)} open conflict(s) remain — chat and report share this status."
            if open_rows
            else "No open conflicts in the current truth layer."
        ),
    }


def build_missing_evidence_section(*, gaps: list[dict[str, Any]]) -> dict[str, Any]:
    open_gaps = [g for g in gaps if g.get("status") in ("NOT_PROVIDED", "OPEN")]
    provided = [g for g in gaps if g.get("status") in ("PROVIDED", "RESOLVED", "CLOSED")]
    return {
        "heading": "Missing evidence checklist",
        "open_count": len(open_gaps),
        "items": [
            {
                "gap_id": g.get("gap_id"),
                "gap_type": g.get("gap_type"),
                "referenced_label": g.get("referenced_label"),
                "required_doc_type": g.get("required_doc_type"),
                "status": g.get("status"),
                "summary": g.get("summary"),
            }
            for g in open_gaps
        ],
        "provided_or_closed": [
            {
                "gap_id": g.get("gap_id"),
                "status": g.get("status"),
                "referenced_label": g.get("referenced_label"),
            }
            for g in provided
        ],
        "conclusion": (
            "Upload items above before treating the pack as complete."
            if open_gaps
            else "No open missing-evidence gaps for the current pack."
        ),
    }


def build_risk_section(*, risk: dict[str, Any] | None) -> dict[str, Any]:
    if not risk:
        return {
            "heading": "Risk drivers",
            "risk_level": "UNKNOWN",
            "score": None,
            "drivers": [],
            "disclaimer": (
                "Risk score is a weighted checklist of unresolved diligence signals, "
                "not a legal conclusion about title."
            ),
            "conclusion": "No risk snapshot yet — run analyze/reconcile first.",
        }
    return {
        "heading": "Risk drivers",
        "risk_level": risk.get("risk_level"),
        "score": risk.get("score"),
        "weights_version": risk.get("weights_version"),
        "drivers": risk.get("drivers") or [],
        "disclaimer": risk.get("disclaimer")
        or (
            "Risk score is a weighted checklist of unresolved diligence signals, "
            "not a legal conclusion about title."
        ),
        "conclusion": f"Presented risk level: {risk.get('risk_level')} (not legal truth).",
    }


def build_recommendations_section(
    *,
    conflicts: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    legal_findings: list[dict[str, Any]],
    open_review_count: int,
) -> dict[str, Any]:
    recs: list[dict[str, Any]] = []
    for c in conflicts:
        if c.get("status") != "OPEN":
            continue
        recs.append(
            {
                "priority": "HIGH",
                "kind": "resolve_conflict",
                "ref": c.get("conflict_id"),
                "action": f"Resolve {c.get('conflict_type')}: {c.get('summary')}",
            }
        )
    for g in gaps:
        if g.get("status") not in ("NOT_PROVIDED", "OPEN"):
            continue
        recs.append(
            {
                "priority": "HIGH",
                "kind": "upload_document",
                "ref": g.get("gap_id"),
                "action": f"Upload {g.get('referenced_label') or g.get('required_doc_type') or 'referenced document'}",
            }
        )
    for f in legal_findings:
        if f.get("status") not in ("UNRESOLVED", "OPEN", "REQUIRES_REVIEW"):
            continue
        recs.append(
            {
                "priority": f.get("severity") or "MEDIUM",
                "kind": "legal_followup",
                "ref": f.get("finding_id"),
                "action": f.get("recommended_action") or f.get("statement"),
            }
        )
    if open_review_count:
        recs.append(
            {
                "priority": "MEDIUM",
                "kind": "human_review",
                "ref": None,
                "action": f"Clear {open_review_count} open review task(s) in the review queue.",
            }
        )
    if not recs:
        recs.append(
            {
                "priority": "LOW",
                "kind": "monitor",
                "ref": None,
                "action": "No blocking gaps detected in the current pack; re-run after new uploads.",
            }
        )
    return {
        "heading": "Recommended next verifications",
        "items": recs,
        "conclusion": "Recommendations are operational diligence steps, not legal advice.",
    }
