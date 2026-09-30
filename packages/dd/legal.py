"""Legal findings — Layer 1 extract (rules) / Layer 2 reason (optional narrative)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ENGINE_VERSION = "1.0"


@dataclass
class DraftLegalFinding:
    category: str
    severity: str
    statement: str
    status: str = "UNRESOLVED"
    layer: str = "extract"
    evidence_ids: list[str] = field(default_factory=list)
    related_fact_ids: list[str] = field(default_factory=list)
    related_conflict_id: str | None = None
    related_gap_id: str | None = None
    missing_evidence: list[str] = field(default_factory=list)
    recommended_action: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "statement": self.statement,
            "status": self.status,
            "layer": self.layer,
            "evidence_ids": self.evidence_ids,
            "related_fact_ids": self.related_fact_ids,
            "related_conflict_id": self.related_conflict_id,
            "related_gap_id": self.related_gap_id,
            "missing_evidence": self.missing_evidence,
            "recommended_action": self.recommended_action,
            "details": self.details,
            "engine_version": ENGINE_VERSION,
        }


def _mortgage_is_active(fact: Any) -> bool:
    if fact.verification_state in ("NOT_FOUND", "NOT_PROVIDED", "NOT_APPLICABLE"):
        return False
    text = (fact.value_text or fact.value_normalized or "").strip().lower()
    if text in ("", "not found", "none", "clear", "no encumbrance", "nil"):
        return False
    return True


def build_legal_findings_layer1(
    *,
    facts: list[Any],
    conflicts: list[Any],
    gaps: list[Any],
    ownership_gaps: list[dict[str, Any]] | None = None,
    share_conflicts: list[dict[str, Any]] | None = None,
) -> list[DraftLegalFinding]:
    """Rule-based findings from facts, conflicts, and missing evidence.

    Critical: mortgage without release → UNRESOLVED + missing release, never 'clear'.
    """
    findings: list[DraftLegalFinding] = []

    mortgage_facts = [f for f in facts if f.fact_type == "encumbrance.mortgage" and _mortgage_is_active(f)]
    mortgage_gaps = [g for g in gaps if getattr(g, "gap_type", None) == "MORTGAGE_RELEASE_MISSING"]

    if mortgage_facts and mortgage_gaps:
        gap = mortgage_gaps[0]
        findings.append(
            DraftLegalFinding(
                category="ENCUMBRANCE",
                severity="HIGH",
                statement=(
                    "A mortgage is referenced in the supplied records. "
                    "No release deed was uploaded — encumbrance status is UNRESOLVED, not clear."
                ),
                status="UNRESOLVED",
                layer="extract",
                related_fact_ids=[f.fact_id for f in mortgage_facts],
                related_gap_id=getattr(gap, "gap_id", None),
                missing_evidence=["release deed", "latest encumbrance certificate"],
                recommended_action="Verify current mortgage discharge status with a release deed or fresh EC.",
                details={
                    "mortgage_values": [f.value_text for f in mortgage_facts],
                    "clear_title_claimed": False,
                },
            )
        )
    elif mortgage_facts:
        # Mortgage present even if gap detector missed it — still UNRESOLVED without release class
        findings.append(
            DraftLegalFinding(
                category="ENCUMBRANCE",
                severity="HIGH",
                statement=(
                    "A mortgage is referenced in the supplied records. "
                    "Discharge / release evidence is not confirmed — status UNRESOLVED."
                ),
                status="UNRESOLVED",
                layer="extract",
                related_fact_ids=[f.fact_id for f in mortgage_facts],
                missing_evidence=["release deed"],
                recommended_action="Obtain mortgage release or confirm discharge on EC.",
                details={"clear_title_claimed": False},
            )
        )

    for g in gaps:
        gap_type = getattr(g, "gap_type", "")
        if gap_type == "MORTGAGE_RELEASE_MISSING":
            continue  # already handled
        if gap_type == "REFERENCED_INSTRUMENT_MISSING":
            findings.append(
                DraftLegalFinding(
                    category="MISSING_INSTRUMENT",
                    severity="HIGH",
                    statement=getattr(g, "summary", None)
                    or f"Referenced instrument not uploaded: {g.referenced_label}",
                    status="UNRESOLVED",
                    layer="extract",
                    related_gap_id=getattr(g, "gap_id", None),
                    missing_evidence=[g.referenced_label],
                    recommended_action=f"Upload or obtain: {g.referenced_label}",
                    details={"required_doc_type": g.required_doc_type},
                )
            )

    conflict_category = {
        "OWNER_NAME_CONFLICT": ("OWNERSHIP", "HIGH", "Resolve conflicting owner identity against source deeds."),
        "SURVEY_NUMBER_CONFLICT": ("IDENTITY", "CRITICAL", "Do not treat survey identity as verified until conflict is resolved."),
        "AREA_CONFLICT": ("IDENTITY", "HIGH", "Reconcile area figures against primary instruments."),
        "DATE_CONFLICT": ("OWNERSHIP", "MEDIUM", "Confirm transaction dates from registration endorsements."),
        "SHARE_CONFLICT": ("OWNERSHIP", "HIGH", "Recompute ownership shares from instrument schedules."),
        "ENCUMBRANCE_STATUS_CONFLICT": ("ENCUMBRANCE", "HIGH", "Clarify encumbrance status with EC / release."),
        "OWNERSHIP_SEQUENCE_CONFLICT": ("OWNERSHIP", "HIGH", "Fill chain gap with intervening instruments."),
        "TEMPORAL_CONFLICT": ("OWNERSHIP", "HIGH", "Investigate inverted chronology before relying on chain."),
    }

    for c in conflicts:
        if getattr(c, "status", "OPEN") not in ("OPEN", None):
            continue
        ctype = c.conflict_type
        cat, sev, action = conflict_category.get(
            ctype, ("GENERAL", getattr(c, "severity", "MEDIUM"), "Human review required.")
        )
        fact_ids: list[str] = []
        for link in getattr(c, "fact_links", None) or []:
            f = getattr(link, "fact", None)
            if f is not None:
                fact_ids.append(f.fact_id)
        findings.append(
            DraftLegalFinding(
                category=cat,
                severity=sev,
                statement=c.summary,
                status="UNRESOLVED",
                layer="extract",
                related_fact_ids=fact_ids,
                related_conflict_id=c.conflict_id,
                recommended_action=action,
                details={"conflict_type": ctype},
            )
        )

    for gap in ownership_gaps or []:
        findings.append(
            DraftLegalFinding(
                category="OWNERSHIP",
                severity="HIGH",
                statement=gap.get("summary") or "Ownership chain gap detected.",
                status="UNRESOLVED",
                layer="extract",
                recommended_action="Obtain intervening transfer instruments to close the chain.",
                details=gap,
            )
        )

    for sc in share_conflicts or []:
        findings.append(
            DraftLegalFinding(
                category="OWNERSHIP",
                severity="HIGH",
                statement=sc.get("summary") or "Share accounting conflict.",
                status="UNRESOLVED",
                layer="extract",
                recommended_action="Recompute shares from the instrument; do not rely on LLM arithmetic.",
                details=sc,
            )
        )

    return findings


def enrich_findings_layer2(findings: list[DraftLegalFinding]) -> list[DraftLegalFinding]:
    """Layer 2: Pro-slot narrative notes (deterministic stub; Gemini Pro when wired).

    Keeps status/evidence from Layer 1 — never upgrades UNRESOLVED mortgage to clear.
    Routed as PRO via packages.ai.routing for cost accounting when LLM is attached.
    """
    from packages.ai.routing import RouteEngine, route_for_stage

    route = route_for_stage("legal_layer2")
    assert route == RouteEngine.PRO  # cost-aware: legal ambiguity → Pro, not Flash

    enriched: list[DraftLegalFinding] = []
    for f in findings:
        note = (
            f"Layer-2 ({route.value}) review of {f.category}: {f.statement} "
            f"Status remains {f.status}."
        )
        details = dict(f.details or {})
        details["layer2_note"] = note
        details["layer2_route"] = route.value
        # Preserve UNRESOLVED for encumbrances — never invent "clear"
        if f.category == "ENCUMBRANCE" and f.status == "UNRESOLVED":
            details["title_clear"] = False
        enriched.append(
            DraftLegalFinding(
                category=f.category,
                severity=f.severity,
                statement=f.statement,
                status=f.status,
                layer="reason",
                evidence_ids=list(f.evidence_ids),
                related_fact_ids=list(f.related_fact_ids),
                related_conflict_id=f.related_conflict_id,
                related_gap_id=f.related_gap_id,
                missing_evidence=list(f.missing_evidence),
                recommended_action=f.recommended_action,
                details=details,
            )
        )
    return enriched
