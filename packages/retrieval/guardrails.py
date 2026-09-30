"""Post-checks on RAG answers — refuse invented critical IDs."""

from __future__ import annotations

import re
from typing import Any

from packages.pipeline.normalization import normalize_identifier
from packages.retrieval.query import extract_identifiers


CRITICAL_ID_PATTERN = re.compile(
    r"\b(?:(?:gata|survey|sy\.?\s*no\.?|khasra)\s*(?:no\.?\s*)?)?(\d{1,4}/\d{1,4})\b"
    r"|\b(KH[-\s]?\d+)\b"
    r"|\b(REG[-\s]?\d+)\b",
    re.I,
)

ANSWER_STATUSES = frozenset(
    {
        "SUPPORTED",
        "PARTIALLY_SUPPORTED",
        "CONFLICTING",
        "INSUFFICIENT_EVIDENCE",
        "NOT_FOUND",
        "REQUIRES_REVIEW",
    }
)


def evidence_corpus(evidence_snippets: list[str], fact_values: list[str]) -> str:
    return "\n".join([*(evidence_snippets or []), *(fact_values or [])]).lower()


def asserted_identifiers(answer: str) -> list[str]:
    found: list[str] = []
    for m in CRITICAL_ID_PATTERN.finditer(answer or ""):
        raw = next((g for g in m.groups() if g), None)
        if not raw:
            continue
        found.append(normalize_identifier(raw))
    # also use shared extractor
    for item in extract_identifiers(answer or ""):
        if item["normalized"] not in found:
            found.append(item["normalized"])
    return found


def apply_guardrails(
    *,
    answer: str,
    status: str,
    evidence_snippets: list[str],
    fact_values: list[str],
    open_conflicts: list[dict[str, Any]],
) -> tuple[str, str, list[str]]:
    """
    Returns (answer, status, warnings).
    Downgrades / refuses if critical IDs in the answer are absent from retrieved evidence.
    """
    warnings: list[str] = []
    status = status if status in ANSWER_STATUSES else "INSUFFICIENT_EVIDENCE"
    corpus = evidence_corpus(evidence_snippets, fact_values)
    corpus_norm = normalize_identifier(corpus)

    for ident in asserted_identifiers(answer):
        if not ident:
            continue
        if ident not in corpus_norm and ident not in corpus.replace(" ", ""):
            warnings.append(f"Answer asserted identifier '{ident}' not present in retrieved evidence.")
            status = "INSUFFICIENT_EVIDENCE"
            answer = (
                f"{answer.rstrip()}\n\n"
                f"[Guardrail] Identifier `{ident}` was not found in retrieved evidence; "
                "do not treat it as established."
            )

    if open_conflicts and status == "SUPPORTED":
        # Don't claim full support when case has open conflicts relevant to pack
        status = "PARTIALLY_SUPPORTED"
        warnings.append("Open conflicts present; status downgraded from SUPPORTED.")

    # Never claim non-existence from not-found
    if re.search(r"\b(does not exist|never existed|no such (owner|deed|parcel))\b", answer, re.I):
        if status in ("NOT_FOUND", "INSUFFICIENT_EVIDENCE"):
            answer = (
                answer.rstrip()
                + "\n\n[Guardrail] Absence from uploaded documents is not proof of non-existence."
            )
            warnings.append("Rewrote absolute non-existence claim.")

    return answer, status, warnings
