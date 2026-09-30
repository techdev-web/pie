"""Optional GIS hooks — geometry validity ≠ legal / survey identity."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GisAssessment:
    """Three independent validations (architecture §22)."""

    geometry_valid: bool | None = None  # polygon mathematically valid?
    identity_match: str = "UNKNOWN"  # MATCH | MISMATCH | AMBIGUOUS | UNKNOWN
    boundary_consistent: str = "UNKNOWN"  # OK | CONFLICT | UNKNOWN
    findings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "geometry_valid": self.geometry_valid,
            "identity_match": self.identity_match,
            "boundary_consistent": self.boundary_consistent,
            "findings": self.findings,
        }


def assess_gis_hooks(
    *,
    geo_payloads: list[dict[str, Any]] | None = None,
    survey_identity_match: str | None = None,
) -> GisAssessment:
    """Evaluate optional GIS payloads without equating validity to legal identity.

    When no geometry is supplied, returns UNKNOWN hooks (not a pass).
    """
    assessment = GisAssessment()
    payloads = geo_payloads or []

    if not payloads:
        if survey_identity_match:
            assessment.identity_match = survey_identity_match
        return assessment

    for i, payload in enumerate(payloads):
        # A. Geometry validity
        valid = payload.get("geometry_valid")
        if valid is False:
            assessment.geometry_valid = False
            assessment.findings.append(
                {
                    "finding_type": "GEOMETRY_INVALID",
                    "severity": "MEDIUM",
                    "status": "OPEN",
                    "statement": "Supplied geometry failed mathematical validity checks.",
                    "geometry_valid": False,
                    "identity_match": "UNKNOWN",
                    "boundary_consistent": "UNKNOWN",
                    "details": {"index": i, "source": payload.get("geometry_source")},
                    "related_fact_ids": [],
                }
            )
        elif valid is True and assessment.geometry_valid is not False:
            assessment.geometry_valid = True

        # B. Geometry identity vs claimed survey
        claimed = payload.get("claimed_survey_no")
        matched = payload.get("matched_survey_no")
        if claimed and matched and claimed != matched:
            assessment.identity_match = "MISMATCH"
            assessment.findings.append(
                {
                    "finding_type": "GEOMETRY_MISMATCH",
                    "severity": "HIGH",
                    "status": "OPEN",
                    "statement": (
                        f"Geometry identity mismatch: claimed survey '{claimed}' "
                        f"vs matched '{matched}'. Valid geometry does not prove legal identity."
                    ),
                    "geometry_valid": valid if isinstance(valid, bool) else None,
                    "identity_match": "MISMATCH",
                    "boundary_consistent": payload.get("boundary_consistent", "UNKNOWN"),
                    "details": payload,
                    "related_fact_ids": [],
                }
            )
        elif claimed and matched and claimed == matched:
            if assessment.identity_match not in ("MISMATCH", "AMBIGUOUS"):
                assessment.identity_match = "MATCH"

        # C. Boundary consistency
        boundary = payload.get("boundary_consistent")
        if boundary in ("CONFLICT", "MISMATCH", False):
            assessment.boundary_consistent = "CONFLICT"
            assessment.findings.append(
                {
                    "finding_type": "BOUNDARY_INCONSISTENT",
                    "severity": "MEDIUM",
                    "status": "OPEN",
                    "statement": (
                        "Written boundary description does not agree with geometry. "
                        "This is separate from geometry validity and survey identity."
                    ),
                    "geometry_valid": valid if isinstance(valid, bool) else None,
                    "identity_match": assessment.identity_match,
                    "boundary_consistent": "CONFLICT",
                    "details": payload,
                    "related_fact_ids": [],
                }
            )
        elif boundary in ("OK", True) and assessment.boundary_consistent != "CONFLICT":
            assessment.boundary_consistent = "OK"

    if survey_identity_match == "AMBIGUOUS" and assessment.identity_match == "UNKNOWN":
        assessment.identity_match = "AMBIGUOUS"

    return assessment
