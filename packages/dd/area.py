"""Area reconciliation — unit convert in code; LLM does not decide tolerances."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from packages.pipeline.normalization import parse_area

# Default absolute tolerance in square metres (~0.001 acre)
DEFAULT_TOLERANCE_SQM = 5.0
# Relative tolerance for large parcels
DEFAULT_RELATIVE_TOLERANCE = 0.02


@dataclass
class AreaObservation:
    fact_id: str
    document_id: str | None
    raw: str
    unit: str | None
    normalized_sq_m: float | None
    normalized_acre: float | None
    normalized_hectare: float | None
    ambiguous: bool = False


@dataclass
class AreaReconciliationResult:
    observations: list[AreaObservation] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "observations": [
                {
                    "fact_id": o.fact_id,
                    "document_id": o.document_id,
                    "raw": o.raw,
                    "unit": o.unit,
                    "normalized_sq_m": o.normalized_sq_m,
                    "normalized_acre": o.normalized_acre,
                    "normalized_hectare": o.normalized_hectare,
                    "ambiguous": o.ambiguous,
                }
                for o in self.observations
            ],
            "conflicts": self.conflicts,
            "findings": self.findings,
        }


def reconcile_areas(
    area_facts: list[Any],
    *,
    tolerance_sqm: float = DEFAULT_TOLERANCE_SQM,
    relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE,
) -> AreaReconciliationResult:
    """Normalize area facts to canonical units and emit AREA_CONFLICT when drifted."""
    result = AreaReconciliationResult()
    usable: list[AreaObservation] = []

    for f in area_facts:
        if f.verification_state in ("NOT_FOUND", "NOT_PROVIDED", "NOT_APPLICABLE"):
            continue
        raw = f.value_text or f.value_normalized or ""
        parsed = parse_area(raw)
        # Prefer already-normalized sqm from extraction pipeline
        sqm = None
        if f.value_normalized:
            try:
                sqm = float(f.value_normalized)
            except ValueError:
                sqm = parsed.sqm
        else:
            sqm = parsed.sqm

        obs = AreaObservation(
            fact_id=f.fact_id,
            document_id=str(f.document_id) if f.document_id else None,
            raw=raw,
            unit=parsed.unit or f.unit,
            normalized_sq_m=sqm,
            normalized_acre=(sqm / 4046.8564224) if sqm is not None else None,
            normalized_hectare=(sqm / 10000.0) if sqm is not None else None,
            ambiguous=parsed.ambiguous or sqm is None,
        )
        result.observations.append(obs)
        if sqm is not None and not obs.ambiguous:
            usable.append(obs)

    if len(usable) < 2:
        return result

    nums = [o.normalized_sq_m for o in usable if o.normalized_sq_m is not None]
    if not nums:
        return result
    lo, hi = min(nums), max(nums)
    span = hi - lo
    mid = (hi + lo) / 2.0 or 1.0
    absolute_ok = span <= tolerance_sqm
    relative_ok = (span / mid) <= relative_tolerance
    if absolute_ok or relative_ok:
        return result

    fact_ids = [o.fact_id for o in usable]
    conflict = {
        "conflict_type": "AREA_CONFLICT",
        "severity": "HIGH",
        "summary": (
            f"AREA_CONFLICT: normalized areas differ by {span:.2f} sqm "
            f"(tolerance {tolerance_sqm} sqm / {relative_tolerance:.0%} relative)."
        ),
        "details": {
            "values_sqm": [round(n, 4) for n in nums],
            "span_sqm": round(span, 4),
            "tolerance_sqm": tolerance_sqm,
            "relative_tolerance": relative_tolerance,
        },
        "related_fact_ids": fact_ids,
    }
    result.conflicts.append(conflict)
    result.findings.append(
        {
            "finding_type": "AREA_CONFLICT",
            "severity": "HIGH",
            "status": "OPEN",
            "statement": conflict["summary"],
            "identity_match": None,
            "geometry_valid": None,
            "boundary_consistent": "UNKNOWN",
            "details": conflict["details"],
            "related_fact_ids": fact_ids,
        }
    )
    return result
