"""Dual-OCR helpers for critical pages (Architecture §10.2)."""

from __future__ import annotations

import re

from packages.domain.textutil import normalize_text

# Critical-field cues from Architecture §10.2
CRITICAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(survey|gata|khata|khasra|plot)\b", re.I),
    re.compile(r"\b(owner|seller|buyer|vendee|vendor)\b", re.I),
    re.compile(r"\b(area|acre|hectare|sq\.?\s*m|sqm|bigha)\b", re.I),
    re.compile(r"\b(registration|reg\.?\s*no|consideration|mortgage|encumbrance)\b", re.I),
    re.compile(r"\b(dated|date|executed on)\b", re.I),
    re.compile(r"\b\d{1,4}\s*/\s*\d{1,4}\b"),  # gata-like 183/2
)

# Identifier-bearing spans used to detect CONFLICTING_OCR
_ID_SPAN = re.compile(
    r"(?:"
    r"(?:gata|survey|khata|khasra|plot)\s*(?:no\.?|number|#)?\s*[:.]?\s*[\w./-]+"
    r"|reg(?:istration)?\.?\s*(?:no\.?|number)?\s*[:.]?\s*[\w./-]+"
    r"|\b\d{1,4}\s*/\s*\d{1,4}\b"
    r")",
    re.I,
)

_LABELED_SURVEY = re.compile(
    r"((?:gata|survey|khata|khasra|plot)\s*(?:no\.?|number|#)?\s*[:.]?\s*)(\d+\s*/\s*\d+)",
    re.I,
)
_BARE_SURVEY = re.compile(r"\b(\d{1,4}\s*/\s*\d{1,4})\b")


def is_critical_page_text(text: str | None) -> bool:
    """True when page text likely contains diligence-critical fields."""
    if not text or not text.strip():
        return False
    return any(p.search(text) for p in CRITICAL_PATTERNS)


def extract_identifier_spans(text: str) -> set[str]:
    """Normalized identifier spans for OCR A/B comparison."""
    spans: set[str] = set()
    for m in _ID_SPAN.finditer(text or ""):
        spans.add(normalize_text(m.group(0)))
    return spans


def ocr_texts_diverge(text_a: str, text_b: str) -> bool:
    """True when identifier spans disagree (not mere whitespace diffs)."""
    a = extract_identifier_spans(text_a)
    b = extract_identifier_spans(text_b)
    if not a and not b:
        return normalize_text(text_a) != normalize_text(text_b)
    if not a or not b:
        return True
    return a != b


def _flip_slash_id(raw: str) -> str:
    """Flip trailing digit after slash: 183/2 → 183/7."""
    left, sep, right = raw.rpartition("/")
    if not sep or not right:
        return raw
    digits = list(right)
    for i in range(len(digits) - 1, -1, -1):
        if digits[i].isdigit():
            digits[i] = str((int(digits[i]) + 5) % 10)
            break
    return f"{left}/{''.join(digits)}"


def diverge_survey_in_text(text: str) -> str:
    """Deterministic mock OCR-B: flip trailing digit of first survey/gata-like token."""
    m = _LABELED_SURVEY.search(text)
    if m:
        flipped = _flip_slash_id(m.group(2))
        start, end = m.start(2), m.end(2)
        return text[:start] + flipped + text[end:]

    m2 = _BARE_SURVEY.search(text)
    if m2:
        flipped = _flip_slash_id(m2.group(1))
        start, end = m2.start(1), m2.end(1)
        return text[:start] + flipped + text[end:]

    return text + " Gata No. 183/7"
