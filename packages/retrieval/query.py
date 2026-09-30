"""Query classification and identifier extraction for hybrid retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from packages.pipeline.normalization import normalize_identifier


# Survey/gata style: 183/2, 521, KH-102, REG-1234, 2001/458
_ID_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:gata|survey|sy\.?\s*no\.?|khasra|plot)\s*(?:no\.?\s*)?([0-9]+(?:/[0-9]+)?)\b", re.I), "survey"),
    (re.compile(r"\b(KH[-\s]?\d+)\b", re.I), "khata"),
    (re.compile(r"\b(REG[-\s]?\d+)\b", re.I), "registration"),
    (re.compile(r"\b(\d{3,4}/\d{1,4})\b"), "survey_or_reg"),
    (re.compile(r"\b(\d{1,4}/\d{1,3})\b"), "survey"),
]


@dataclass
class QueryAnalysis:
    raw: str
    query_class: str
    extracted_ids: list[dict[str, str]] = field(default_factory=list)
    wants_owner: bool = False
    wants_upload_next: bool = False
    wants_conflicts: bool = False
    wants_survey: bool = False
    keywords: list[str] = field(default_factory=list)


def extract_identifiers(text: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for pattern, id_type in _ID_PATTERNS:
        for m in pattern.finditer(text or ""):
            raw = m.group(1).strip()
            norm = normalize_identifier(raw)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            found.append({"id_type": id_type, "raw": raw, "normalized": norm})
    return found


def classify_query(query: str) -> QueryAnalysis:
    q = (query or "").strip()
    lower = q.lower()
    ids = extract_identifiers(q)

    wants_owner = bool(
        re.search(r"\b(owner|owns|ownership|buyer|seller|who\s+(is|owns))\b", lower)
    )
    wants_upload = bool(
        re.search(
            r"\b(upload\s+next|what\s+should\s+i\s+upload|missing\s+(doc|deed|evidence)|need\s+to\s+upload)\b",
            lower,
        )
    )
    wants_conflicts = bool(re.search(r"\b(conflict|disagree|discrepan|inconsist)\b", lower))
    wants_survey = bool(ids) or bool(re.search(r"\b(survey|gata|khata|khasra|parcel)\b", lower))

    if wants_upload:
        qclass = "upload_next"
    elif wants_conflicts:
        qclass = "conflicts"
    elif wants_owner and not ids:
        qclass = "ownership"
    elif ids:
        qclass = "identifier"
    elif wants_survey:
        qclass = "parcel"
    else:
        qclass = "general"

    tokens = re.findall(r"[a-z0-9]{3,}", lower)
    stop = {
        "the",
        "and",
        "what",
        "who",
        "about",
        "this",
        "that",
        "with",
        "from",
        "have",
        "does",
        "current",
        "please",
        "tell",
        "me",
    }
    keywords = [t for t in tokens if t not in stop][:12]

    return QueryAnalysis(
        raw=q,
        query_class=qclass,
        extracted_ids=ids,
        wants_owner=wants_owner,
        wants_upload_next=wants_upload,
        wants_conflicts=wants_conflicts,
        wants_survey=wants_survey,
        keywords=keywords,
    )
