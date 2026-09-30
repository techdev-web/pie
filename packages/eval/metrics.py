"""Eval metrics: fact match, citation precision, conflict recall, ID hit rate."""

from __future__ import annotations

from typing import Any


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def fact_match_score(
    expected_facts: list[dict[str, Any]], actual_facts: list[dict[str, Any]]
) -> dict[str, Any]:
    """Simple recall/precision on (fact_type, normalized value) pairs."""
    exp = {
        (_norm(f.get("fact_type")), _norm(f.get("value_normalized") or f.get("value_text")))
        for f in expected_facts
        if f.get("fact_type")
    }
    act = {
        (_norm(f.get("fact_type")), _norm(f.get("value_normalized") or f.get("value_text")))
        for f in actual_facts
        if f.get("fact_type")
    }
    if not exp and not act:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "matched": 0, "expected": 0}
    matched = exp & act
    precision = len(matched) / len(act) if act else 0.0
    recall = len(matched) / len(exp) if exp else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "matched": len(matched),
        "expected": len(exp),
        "actual": len(act),
        "missing": sorted(list(exp - act)),
        "extra": sorted(list(act - exp)),
    }


def conflict_recall(
    expected_types: list[str], actual_conflicts: list[dict[str, Any]]
) -> dict[str, Any]:
    exp = {_norm(t) for t in expected_types}
    act = {_norm(c.get("conflict_type")) for c in actual_conflicts if c.get("conflict_type")}
    matched = exp & act
    recall = len(matched) / len(exp) if exp else 1.0
    return {
        "recall": recall,
        "matched": sorted(matched),
        "missing": sorted(exp - act),
        "actual_types": sorted(act),
    }


def citation_precision(
    claims: list[dict[str, Any]],
    valid_evidence_ids: set[str],
) -> dict[str, Any]:
    """Fraction of cited evidence IDs that exist. Uncited material claims count against."""
    cited = 0
    valid = 0
    uncited_claims = 0
    for claim in claims:
        eids = claim.get("evidence_ids") or []
        if not eids and claim.get("requires_citation", True):
            uncited_claims += 1
            continue
        for eid in eids:
            cited += 1
            if eid in valid_evidence_ids:
                valid += 1
    precision = (valid / cited) if cited else (0.0 if uncited_claims else 1.0)
    hallucination_rate = uncited_claims / len(claims) if claims else 0.0
    return {
        "precision": precision,
        "cited": cited,
        "valid": valid,
        "uncited_claims": uncited_claims,
        "hallucination_rate": hallucination_rate,
    }


def id_retrieval_hit_rate(
    queries: list[dict[str, Any]], results: list[dict[str, Any]]
) -> dict[str, Any]:
    """queries: [{query, expected_id}], results: [{query, hit_ids}]"""
    if not queries:
        return {"hit_rate": 1.0, "hits": 0, "total": 0}
    by_q = {r.get("query"): set(r.get("hit_ids") or []) for r in results}
    hits = 0
    for q in queries:
        expected = q.get("expected_id")
        got = by_q.get(q.get("query"), set())
        if expected and expected in got:
            hits += 1
        elif expected and any(_norm(expected) in _norm(h) for h in got):
            hits += 1
    return {"hit_rate": hits / len(queries), "hits": hits, "total": len(queries)}


DEFAULT_GATES = {
    "min_citation_precision": 0.9,
    "min_conflict_recall": 0.8,
    "max_hallucination_rate": 0.1,
    "min_fact_f1": 0.5,
}


def gate_passed(metrics: dict[str, Any], thresholds: dict[str, float] | None = None) -> bool:
    t = {**DEFAULT_GATES, **(thresholds or {})}
    if metrics.get("citation_precision", 1.0) < t["min_citation_precision"]:
        return False
    if metrics.get("conflict_recall", 1.0) < t["min_conflict_recall"]:
        return False
    if metrics.get("hallucination_rate", 0.0) > t["max_hallucination_rate"]:
        return False
    if metrics.get("fact_f1", 1.0) < t["min_fact_f1"]:
        return False
    return True
