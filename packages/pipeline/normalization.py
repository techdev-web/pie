"""Deterministic normalization services for Phase 2 (code, not LLM)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from packages.domain.textutil import normalize_text

# Verification states supported from Phase 2 onward
VERIFICATION_STATES = frozenset(
    {
        "EXTRACTED",
        "SUPPORTED",
        "CORROBORATED",
        "VERIFIED",
        "CONFLICTING",
        "AMBIGUOUS",
        "UNVERIFIED",
        "NOT_FOUND",
        "NOT_PROVIDED",
        "NOT_APPLICABLE",
        "REQUIRES_REVIEW",
    }
)

AREA_TO_SQM = {
    "sqm": 1.0,
    "sq.m": 1.0,
    "sq.m.": 1.0,
    "square meter": 1.0,
    "square metre": 1.0,
    "sqft": 0.092903,
    "sq.ft": 0.092903,
    "sq.ft.": 0.092903,
    "square feet": 0.092903,
    "acre": 4046.8564224,
    "acres": 4046.8564224,
    "hectare": 10000.0,
    "hectares": 10000.0,
    "bigha": 2508.38,  # UP approximate; flagged in value_json
    "biswa": 125.419,
    "gunta": 101.17,
    "guntha": 101.17,
}


@dataclass
class ParsedDate:
    raw: str
    parsed: date | None
    iso: str | None
    ambiguous: bool = False


@dataclass
class ParsedArea:
    raw: str
    value: float | None
    unit: str | None
    sqm: float | None
    ambiguous: bool = False


def normalize_person_name(name: str) -> str:
    name = normalize_text(name)
    name = re.sub(r"\b(shri|smt|mr|mrs|ms|dr)\b\.?", "", name)
    name = re.sub(r"[^a-z0-9\s./-]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def normalize_identifier(value: str) -> str:
    """Normalize survey/gata/khata/registration style identifiers."""
    value = normalize_text(value)
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"\s*/\s*", "/", value)
    value = re.sub(r"\s*-\s*", "-", value)
    value = re.sub(r"\s+", "", value)
    # common prefixes
    value = re.sub(r"^(gata|survey|sy|s\.?no\.?|khata|khasra|plot)no\.?", "", value)
    value = re.sub(r"^(gata|survey|sy|khata|khasra|plot)", "", value)
    return value.strip(" .-")


def parse_date(raw: str | None) -> ParsedDate:
    if not raw or not str(raw).strip():
        return ParsedDate(raw=raw or "", parsed=None, iso=None)
    text = str(raw).strip()
    patterns = [
        (r"^(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})$", "dmy"),
        (r"^(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})$", "ymd"),
        (r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$", "dMonY"),
    ]
    months = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    for pattern, kind in patterns:
        m = re.match(pattern, text)
        if not m:
            continue
        try:
            if kind == "dmy":
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                # Ambiguous if both day and month <= 12 and differ
                ambiguous = d <= 12 and mo <= 12 and d != mo
                parsed = date(y, mo, d)
            elif kind == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                ambiguous = False
                parsed = date(y, mo, d)
            else:
                d = int(m.group(1))
                mo = months.get(m.group(2).lower())
                y = int(m.group(3))
                if mo is None:
                    return ParsedDate(raw=text, parsed=None, iso=None, ambiguous=True)
                ambiguous = False
                parsed = date(y, mo, d)
            return ParsedDate(raw=text, parsed=parsed, iso=parsed.isoformat(), ambiguous=ambiguous)
        except ValueError:
            return ParsedDate(raw=text, parsed=None, iso=None, ambiguous=True)
    return ParsedDate(raw=text, parsed=None, iso=None, ambiguous=True)


def parse_area(raw: str | None) -> ParsedArea:
    if not raw or not str(raw).strip():
        return ParsedArea(raw=raw or "", value=None, unit=None, sqm=None)
    text = str(raw).strip()
    m = re.search(
        r"([\d,.]+)\s*(sq\.?\s*m\.?|sq\.?\s*ft\.?|square\s+met(?:er|re)s?|square\s+feet|"
        r"acres?|hectares?|bigha|biswa|guntas?|gunthas?|sqm|sqft)?",
        text,
        re.IGNORECASE,
    )
    if not m:
        return ParsedArea(raw=text, value=None, unit=None, sqm=None, ambiguous=True)
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return ParsedArea(raw=text, value=None, unit=None, sqm=None, ambiguous=True)
    unit_raw = (m.group(2) or "sqm").lower()
    unit_key = re.sub(r"\s+", " ", unit_raw).strip()
    unit_key = unit_key.replace("metres", "metre").replace("meters", "meter")
    factor = AREA_TO_SQM.get(unit_key)
    if factor is None:
        # try stripped dots
        compact = unit_key.replace(" ", "")
        factor = AREA_TO_SQM.get(compact)
        unit_key = compact if factor else unit_key
    if factor is None:
        return ParsedArea(raw=text, value=value, unit=unit_key, sqm=None, ambiguous=True)
    return ParsedArea(raw=text, value=value, unit=unit_key, sqm=value * factor)


def parse_share(raw: str | None) -> tuple[int | None, int | None, str | None]:
    if not raw:
        return None, None, None
    text = str(raw).strip()
    m = re.match(r"^(\d+)\s*/\s*(\d+)$", text)
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        if den == 0:
            return None, None, text
        return num, den, text
    m = re.match(r"^(\d+(?:\.\d+)?)\s*%$", text)
    if m:
        pct = float(m.group(1))
        # represent as pct/100 with integers when possible
        num = int(round(pct * 100))
        return num, 10000, text
    return None, None, text


def date_to_utc_datetime(d: date | None) -> datetime | None:
    if d is None:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def validate_candidate_fact(candidate: dict[str, Any]) -> list[str]:
    """Return list of validation errors (empty = ok)."""
    errors: list[str] = []
    if not candidate.get("fact_type"):
        errors.append("missing fact_type")
    if not candidate.get("predicate"):
        errors.append("missing predicate")
    state = candidate.get("verification_state") or "EXTRACTED"
    if state not in VERIFICATION_STATES:
        errors.append(f"invalid verification_state: {state}")
    value = candidate.get("value_text")
    if state not in ("NOT_FOUND", "NOT_PROVIDED", "NOT_APPLICABLE") and not (
        value or candidate.get("value_normalized")
    ):
        errors.append("value required unless NOT_FOUND/NOT_PROVIDED/NOT_APPLICABLE")
    return errors
