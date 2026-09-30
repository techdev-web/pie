"""Survey / parcel identity engine + transformation tracking."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from packages.domain.textutil import normalize_text

SURVEY_ID_TYPES = frozenset(
    {
        "survey_number",
        "survey_no",
        "gata_number",
        "gata_no",
        "khasra_number",
        "khasra_no",
        "khata_number",
        "khata_no",
        "old_survey_number",
        "new_survey_number",
    }
)

# 183 → 183/1, 183/2 pattern
_SUBDIV_RE = re.compile(r"^(.+?)/(\d+)$")


@dataclass
class SurveyIdentityResult:
    parcels: list[dict[str, Any]] = field(default_factory=list)
    transformations: list[dict[str, Any]] = field(default_factory=list)
    ambiguities: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parcels": self.parcels,
            "transformations": self.transformations,
            "ambiguities": self.ambiguities,
            "findings": self.findings,
        }


def _norm_id(value: str) -> str:
    return normalize_text(value).replace(" ", "")


def analyze_survey_identity(
    parcels: list[Any],
    *,
    survey_facts: list[Any] | None = None,
) -> SurveyIdentityResult:
    """Normalize parcel identifiers and flag ambiguous / conflicting survey identity.

    Mapping without evidence stays AMBIGUOUS — never silently VERIFIED.
    """
    result = SurveyIdentityResult()
    all_norms: dict[str, list[str]] = {}  # normalized value → parcel ids

    for parcel in parcels:
        ids = []
        for ident in parcel.identifiers or []:
            norm = _norm_id(ident.id_value or ident.normalized_value or "")
            if not norm:
                continue
            ids.append(
                {
                    "id_type": ident.id_type,
                    "id_value": ident.id_value,
                    "normalized_value": norm,
                }
            )
            all_norms.setdefault(norm, []).append(str(parcel.id))

            # Detect subdivision transformations from value shape
            m = _SUBDIV_RE.match(norm)
            if m:
                parent = m.group(1)
                result.transformations.append(
                    {
                        "from": parent,
                        "to": norm,
                        "parcel_id": str(parcel.id),
                        "mapping_status": "AMBIGUOUS",
                        "note": (
                            "Subdivision pattern inferred from identifier shape; "
                            "requires explicit transformation evidence to verify."
                        ),
                    }
                )

        result.parcels.append(
            {
                "parcel_id": str(parcel.id),
                "display_label": parcel.display_label,
                "identifiers": ids,
            }
        )

    # Same normalized survey number claimed by multiple parcels → ambiguity
    for norm, parcel_ids in all_norms.items():
        unique = sorted(set(parcel_ids))
        if len(unique) > 1:
            result.ambiguities.append(
                {
                    "normalized_value": norm,
                    "parcel_ids": unique,
                    "mapping_status": "AMBIGUOUS",
                }
            )
            result.findings.append(
                {
                    "finding_type": "SURVEY_MAPPING_AMBIGUITY",
                    "severity": "HIGH",
                    "status": "OPEN",
                    "statement": (
                        f"Survey identifier '{norm}' is linked to multiple parcel records "
                        f"({len(unique)}). Mapping status is AMBIGUOUS, not verified."
                    ),
                    "identity_match": "AMBIGUOUS",
                    "geometry_valid": None,
                    "boundary_consistent": None,
                    "details": {"normalized_value": norm, "parcel_ids": unique},
                    "related_fact_ids": [],
                }
            )

    # Cross-document survey fact conflicts feed identity findings
    if survey_facts:
        values: dict[str, list[str]] = {}
        for f in survey_facts:
            v = (f.value_normalized or f.value_text or "").strip()
            if not v:
                continue
            values.setdefault(_norm_id(v), []).append(f.fact_id)
        if len(values) > 1:
            result.findings.append(
                {
                    "finding_type": "SURVEY_NUMBER_CONFLICT",
                    "severity": "HIGH",
                    "status": "OPEN",
                    "statement": (
                        "Documents disagree on survey / parcel identifier. "
                        "Identity match is AMBIGUOUS pending human review."
                    ),
                    "identity_match": "AMBIGUOUS",
                    "geometry_valid": None,
                    "boundary_consistent": None,
                    "details": {"values": list(values.keys())},
                    "related_fact_ids": [fid for ids in values.values() for fid in ids],
                }
            )

    return result
