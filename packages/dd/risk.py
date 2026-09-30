"""Versioned weighted risk engine — drivers, not one opaque score."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

WEIGHTS_VERSION = "risk_weights.v1"
ENGINE_VERSION = "1.0"

DISCLAIMER = (
    "Risk score is a weighted checklist of unresolved diligence signals, "
    "not a legal conclusion about title."
)

# Configurable & versioned — never buried in prompts
RISK_WEIGHTS_V1: dict[str, int] = {
    "MISSING_DEED_CHAIN": 30,
    "UNRESOLVED_MORTGAGE": 30,
    "OWNER_IDENTITY_CONFLICT": 25,
    "AREA_CONFLICT": 15,
    "SURVEY_AMBIGUITY": 20,
    "MISSING_EC": 20,
    "OCR_UNCERTAINTY_CRITICAL_ID": 20,
    "GEOMETRY_MISMATCH": 25,
    "SHARE_CONFLICT": 20,
    "TEMPORAL_CONFLICT": 25,
    "OWNERSHIP_SEQUENCE_CONFLICT": 25,
    "MISSING_INSTRUMENT": 20,
}


@dataclass
class RiskDriver:
    code: str
    label: str
    weight: int
    source_kind: str  # finding | conflict | gap | geo | share
    source_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "weight": self.weight,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "details": self.details,
        }


@dataclass
class RiskAssessment:
    risk_level: str
    score: int
    weights_version: str
    drivers: list[RiskDriver]
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "score": self.score,
            "weights_version": self.weights_version,
            "drivers": [d.to_dict() for d in self.drivers],
            "disclaimer": self.disclaimer,
            "engine_version": ENGINE_VERSION,
        }


def _level_for(score: int) -> str:
    if score >= 70:
        return "CRITICAL"
    if score >= 40:
        return "HIGH"
    if score >= 20:
        return "MEDIUM"
    return "LOW"


def _add_unique(
    drivers: list[RiskDriver],
    seen: set[str],
    *,
    code: str,
    label: str,
    source_kind: str,
    source_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    key = f"{code}:{source_id or label}"
    if key in seen:
        return
    seen.add(key)
    weight = RISK_WEIGHTS_V1.get(code, 10)
    drivers.append(
        RiskDriver(
            code=code,
            label=label,
            weight=weight,
            source_kind=source_kind,
            source_id=source_id,
            details=details or {},
        )
    )


def assess_risk(
    *,
    legal_findings: list[Any],
    geo_findings: list[dict[str, Any]] | None = None,
    conflicts: list[Any] | None = None,
    gaps: list[Any] | None = None,
    share_conflicts: list[dict[str, Any]] | None = None,
) -> RiskAssessment:
    """Build weighted drivers from findings/conflicts/gaps. Score is never legal truth."""
    drivers: list[RiskDriver] = []
    seen: set[str] = set()

    for f in legal_findings:
        status = getattr(f, "status", None) or f.get("status") if isinstance(f, dict) else "UNRESOLVED"
        if status not in ("UNRESOLVED", "OPEN"):
            continue
        category = getattr(f, "category", None) or (f.get("category") if isinstance(f, dict) else "")
        statement = getattr(f, "statement", None) or (f.get("statement") if isinstance(f, dict) else "")
        finding_id = getattr(f, "finding_id", None) or (f.get("finding_id") if isinstance(f, dict) else None)
        details = getattr(f, "details", None) or (f.get("details") if isinstance(f, dict) else {}) or {}

        if category == "ENCUMBRANCE":
            _add_unique(
                drivers,
                seen,
                code="UNRESOLVED_MORTGAGE",
                label="Unresolved mortgage",
                source_kind="finding",
                source_id=finding_id,
                details={"statement": statement, "clear_title_claimed": False},
            )
        elif category == "MISSING_INSTRUMENT":
            label_text = statement or "Missing instrument"
            code = "MISSING_DEED_CHAIN"
            missing = getattr(f, "missing_evidence", None) or (
                f.get("missing_evidence") if isinstance(f, dict) else []
            ) or []
            joined = " ".join(str(x).lower() for x in missing)
            if "encumbrance" in joined or " e.c" in f" {joined}" or joined.strip() == "ec":
                code = "MISSING_EC"
            _add_unique(
                drivers,
                seen,
                code=code,
                label="Missing deed chain" if code == "MISSING_DEED_CHAIN" else "Missing EC",
                source_kind="finding",
                source_id=finding_id,
                details={"statement": label_text},
            )
        elif category == "OWNERSHIP":
            ctype = (details or {}).get("conflict_type") or ""
            if "SHARE" in ctype or "share" in statement.lower():
                code = "SHARE_CONFLICT"
                label = "Share accounting conflict"
            elif "TEMPORAL" in ctype or "chronolog" in statement.lower():
                code = "TEMPORAL_CONFLICT"
                label = "Temporal ownership conflict"
            elif "SEQUENCE" in ctype or "chain" in statement.lower():
                code = "OWNERSHIP_SEQUENCE_CONFLICT"
                label = "Ownership sequence / chain gap"
            else:
                code = "OWNER_IDENTITY_CONFLICT"
                label = "Conflicting owner name"
            _add_unique(
                drivers,
                seen,
                code=code,
                label=label,
                source_kind="finding",
                source_id=finding_id,
                details={"statement": statement},
            )
        elif category == "IDENTITY":
            if "AREA" in statement.upper() or (details or {}).get("conflict_type") == "AREA_CONFLICT":
                _add_unique(
                    drivers,
                    seen,
                    code="AREA_CONFLICT",
                    label="Area conflict",
                    source_kind="finding",
                    source_id=finding_id,
                )
            else:
                _add_unique(
                    drivers,
                    seen,
                    code="SURVEY_AMBIGUITY",
                    label="Survey ambiguity",
                    source_kind="finding",
                    source_id=finding_id,
                )

    for g in geo_findings or []:
        ftype = g.get("finding_type", "")
        if ftype in ("SURVEY_MAPPING_AMBIGUITY", "SURVEY_NUMBER_CONFLICT"):
            _add_unique(
                drivers,
                seen,
                code="SURVEY_AMBIGUITY",
                label="Survey ambiguity",
                source_kind="geo",
                source_id=g.get("finding_id"),
                details=g.get("details") or {},
            )
        elif ftype == "AREA_CONFLICT":
            _add_unique(
                drivers,
                seen,
                code="AREA_CONFLICT",
                label="Area conflict",
                source_kind="geo",
                source_id=g.get("finding_id"),
            )
        elif ftype == "GEOMETRY_MISMATCH":
            _add_unique(
                drivers,
                seen,
                code="GEOMETRY_MISMATCH",
                label="Geometry mismatch",
                source_kind="geo",
                source_id=g.get("finding_id"),
            )
        elif g.get("identity_match") == "MISMATCH":
            _add_unique(
                drivers,
                seen,
                code="GEOMETRY_MISMATCH",
                label="Geometry identity mismatch",
                source_kind="geo",
                source_id=g.get("finding_id"),
            )

    for c in conflicts or []:
        if getattr(c, "status", "OPEN") not in ("OPEN", None):
            continue
        ctype = c.conflict_type
        mapping = {
            "OWNER_NAME_CONFLICT": ("OWNER_IDENTITY_CONFLICT", "Conflicting owner name"),
            "SURVEY_NUMBER_CONFLICT": ("SURVEY_AMBIGUITY", "Survey ambiguity"),
            "AREA_CONFLICT": ("AREA_CONFLICT", "Area conflict"),
            "SHARE_CONFLICT": ("SHARE_CONFLICT", "Share accounting conflict"),
            "OWNERSHIP_SEQUENCE_CONFLICT": (
                "OWNERSHIP_SEQUENCE_CONFLICT",
                "Ownership sequence / chain gap",
            ),
            "TEMPORAL_CONFLICT": ("TEMPORAL_CONFLICT", "Temporal ownership conflict"),
            "ENCUMBRANCE_STATUS_CONFLICT": ("UNRESOLVED_MORTGAGE", "Unresolved mortgage"),
        }
        if ctype in mapping:
            code, label = mapping[ctype]
            _add_unique(
                drivers,
                seen,
                code=code,
                label=label,
                source_kind="conflict",
                source_id=c.conflict_id,
            )

    for g in gaps or []:
        gtype = getattr(g, "gap_type", "")
        if gtype == "MORTGAGE_RELEASE_MISSING":
            _add_unique(
                drivers,
                seen,
                code="UNRESOLVED_MORTGAGE",
                label="Unresolved mortgage",
                source_kind="gap",
                source_id=getattr(g, "gap_id", None),
            )
        elif gtype == "REFERENCED_INSTRUMENT_MISSING":
            label = getattr(g, "referenced_label", "") or ""
            code = "MISSING_EC" if "encumbrance" in label.lower() or label.upper() == "EC" else "MISSING_DEED_CHAIN"
            _add_unique(
                drivers,
                seen,
                code=code,
                label="Missing EC" if code == "MISSING_EC" else "Missing deed chain",
                source_kind="gap",
                source_id=getattr(g, "gap_id", None),
            )

    for sc in share_conflicts or []:
        _add_unique(
            drivers,
            seen,
            code="SHARE_CONFLICT" if sc.get("conflict_type") == "SHARE_CONFLICT" else "SHARE_CONFLICT",
            label="Share accounting conflict",
            source_kind="share",
            source_id=sc.get("event_id"),
            details=sc,
        )
        if sc.get("conflict_type") == "SHARE_OVERSELL_CONFLICT":
            # already SHARE_CONFLICT weight; keep single driver
            pass

    # Deduplicate by code for scoring presentation: keep highest / first of each code
    # Guide presentation shows distinct drivers — keep all unique codes once
    by_code: dict[str, RiskDriver] = {}
    for d in drivers:
        if d.code not in by_code:
            by_code[d.code] = d
    unique_drivers = list(by_code.values())
    score = sum(d.weight for d in unique_drivers)
    return RiskAssessment(
        risk_level=_level_for(score),
        score=score,
        weights_version=WEIGHTS_VERSION,
        drivers=unique_drivers,
        disclaimer=DISCLAIMER,
    )
