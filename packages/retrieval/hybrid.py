"""Hybrid retrieval over facts, evidence, exact IDs, and graph neighborhood."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.ai.provider import get_llm_provider
from packages.domain.models import (
    Conflict,
    ConflictFact,
    Embedding,
    EvidenceItem,
    Fact,
    FactEvidence,
    GraphEdge,
    GraphNode,
    MissingEvidence,
    ParcelIdentifier,
)
from packages.pipeline.normalization import normalize_identifier
from packages.retrieval.embeddings import EMBEDDING_MODEL, cosine_similarity
from packages.retrieval.query import QueryAnalysis, classify_query

__all__ = ["hybrid_retrieve", "RetrievalHit", "RetrievalPack"]


@dataclass
class RetrievalHit:
    kind: str  # fact | evidence | conflict | missing | graph
    score: float
    source: str  # exact_id | keyword | vector | entity | conflict_pack
    fact_id: str | None = None
    evidence_id: str | None = None
    document_id: uuid.UUID | None = None
    page: int | None = None
    snippet: str = ""
    bbox: list[Any] | None = None
    verification_state: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalPack:
    analysis: QueryAnalysis
    hits: list[RetrievalHit]
    open_conflicts: list[dict[str, Any]]
    missing_evidence: list[dict[str, Any]]
    notes: dict[str, Any] = field(default_factory=dict)


def _keyword_score(text: str, keywords: list[str]) -> float:
    if not text or not keywords:
        return 0.0
    lower = text.lower()
    hits = sum(1 for k in keywords if k in lower)
    return hits / max(len(keywords), 1)


async def hybrid_retrieve(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    query: str,
    limit: int = 20,
) -> RetrievalPack:
    analysis = classify_query(query)
    hits: list[RetrievalHit] = []
    notes: dict[str, Any] = {"paths": []}

    facts = list(
        (
            await session.execute(
                select(Fact)
                .options(selectinload(Fact.evidence_links).selectinload(FactEvidence.evidence_item))
                .where(Fact.case_id == case_id, Fact.tenant_id == tenant_id)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    evidence = list(
        (
            await session.execute(
                select(EvidenceItem).where(
                    EvidenceItem.case_id == case_id, EvidenceItem.tenant_id == tenant_id
                )
            )
        )
        .scalars()
        .all()
    )
    evidence_by_id = {e.id: e for e in evidence}

    # --- Exact identifier path (mandatory when IDs present) ---
    if analysis.extracted_ids:
        notes["paths"].append("exact_id")
        norms = {i["normalized"] for i in analysis.extracted_ids}
        # Parcel identifiers
        parcel_ids = list(
            (
                await session.execute(
                    select(ParcelIdentifier).where(
                        ParcelIdentifier.tenant_id == tenant_id,
                        ParcelIdentifier.normalized_value.in_(list(norms)),
                    )
                )
            )
            .scalars()
            .all()
        )
        for pid in parcel_ids:
            hits.append(
                RetrievalHit(
                    kind="fact",
                    score=1.0,
                    source="exact_id",
                    snippet=f"{pid.id_type}={pid.id_value}",
                    meta={"parcel_id": str(pid.parcel_id), "normalized": pid.normalized_value},
                )
            )
        for fact in facts:
            candidates = [
                normalize_identifier(fact.value_text or ""),
                normalize_identifier(fact.value_normalized or ""),
            ]
            if any(c and c in norms for c in candidates):
                hits.extend(_fact_hits(fact, evidence_by_id, score=1.0, source="exact_id"))
            # also scan evidence text
        for ev in evidence:
            norm_text = normalize_identifier(ev.normalized_text or ev.text or "")
            raw_lower = (ev.text or "").lower()
            if any(n in norm_text or n in raw_lower.replace(" ", "") for n in norms):
                hits.append(
                    RetrievalHit(
                        kind="evidence",
                        score=0.98,
                        source="exact_id",
                        evidence_id=ev.evidence_id,
                        document_id=ev.document_id,
                        page=ev.page_number,
                        snippet=(ev.text or "")[:400],
                        bbox=ev.bbox,
                    )
                )

    # --- Ownership-focused facts ---
    if analysis.wants_owner or analysis.query_class == "ownership":
        notes["paths"].append("ownership_facts")
        owner_types = {"party.owner", "party.buyer", "party.seller"}
        for fact in facts:
            if fact.fact_type in owner_types:
                # Prefer VERIFIED
                boost = {
                    "VERIFIED": 1.0,
                    "CORROBORATED": 0.92,
                    "SUPPORTED": 0.85,
                    "EXTRACTED": 0.7,
                    "CONFLICTING": 0.95,
                    "REQUIRES_REVIEW": 0.8,
                }.get(fact.verification_state, 0.5)
                hits.extend(_fact_hits(fact, evidence_by_id, score=boost, source="entity"))

    # --- Keyword / BM25-lite over facts + evidence ---
    notes["paths"].append("keyword")
    for fact in facts:
        blob = " ".join(
            filter(
                None,
                [fact.fact_type, fact.predicate, fact.value_text, fact.value_normalized],
            )
        )
        score = _keyword_score(blob, analysis.keywords)
        if score > 0:
            hits.extend(
                _fact_hits(fact, evidence_by_id, score=0.4 + 0.5 * score, source="keyword")
            )
    for ev in evidence:
        score = _keyword_score(ev.text or "", analysis.keywords)
        if score > 0:
            hits.append(
                RetrievalHit(
                    kind="evidence",
                    score=0.35 + 0.5 * score,
                    source="keyword",
                    evidence_id=ev.evidence_id,
                    document_id=ev.document_id,
                    page=ev.page_number,
                    snippet=(ev.text or "")[:400],
                    bbox=ev.bbox,
                )
            )

    # --- Vector similarity ---
    embeddings = list(
        (
            await session.execute(
                select(Embedding).where(
                    Embedding.case_id == case_id,
                    Embedding.tenant_id == tenant_id,
                    Embedding.model == EMBEDDING_MODEL,
                )
            )
        )
        .scalars()
        .all()
    )
    if embeddings and analysis.raw.strip():
        notes["paths"].append("vector")
        provider = get_llm_provider()
        qvec = (await provider.embed([analysis.raw]))[0]
        scored: list[tuple[float, Embedding]] = []
        for emb in embeddings:
            sim = cosine_similarity(qvec, list(emb.embedding or []))
            if sim > 0.15:
                scored.append((sim, emb))
        scored.sort(key=lambda x: x[0], reverse=True)
        for sim, emb in scored[:15]:
            if emb.source_type == "fact":
                fact = next((f for f in facts if f.id == emb.source_id), None)
                if fact:
                    hits.extend(
                        _fact_hits(fact, evidence_by_id, score=0.3 + 0.6 * sim, source="vector")
                    )
            elif emb.source_type == "evidence":
                ev = evidence_by_id.get(emb.source_id)
                if ev:
                    hits.append(
                        RetrievalHit(
                            kind="evidence",
                            score=0.3 + 0.6 * sim,
                            source="vector",
                            evidence_id=ev.evidence_id,
                            document_id=ev.document_id,
                            page=ev.page_number,
                            snippet=(ev.text or "")[:400],
                            bbox=ev.bbox,
                        )
                    )

    # --- Graph neighborhood for identifier / parcel queries ---
    if analysis.extracted_ids or analysis.query_class in ("identifier", "parcel"):
        notes["paths"].append("graph")
        nodes = list(
            (
                await session.execute(
                    select(GraphNode).where(
                        GraphNode.case_id == case_id, GraphNode.tenant_id == tenant_id
                    )
                )
            )
            .scalars()
            .all()
        )
        edges = list(
            (
                await session.execute(
                    select(GraphEdge).where(
                        GraphEdge.case_id == case_id, GraphEdge.tenant_id == tenant_id
                    )
                )
            )
            .scalars()
            .all()
        )
        id_norms = {i["normalized"] for i in analysis.extracted_ids}
        seed_ids = set()
        for n in nodes:
            label_norm = normalize_identifier(n.label or "")
            if id_norms and any(i in label_norm or i in (n.label or "").lower() for i in id_norms):
                seed_ids.add(n.node_id)
            props = n.properties or {}
            for v in props.values():
                if isinstance(v, str) and normalize_identifier(v) in id_norms:
                    seed_ids.add(n.node_id)
        neighbor = set(seed_ids)
        for e in edges:
            if e.from_node_id in seed_ids:
                neighbor.add(e.to_node_id)
            if e.to_node_id in seed_ids:
                neighbor.add(e.from_node_id)
        for n in nodes:
            if n.node_id in neighbor:
                hits.append(
                    RetrievalHit(
                        kind="graph",
                        score=0.75,
                        source="entity",
                        snippet=f"{n.node_type}: {n.label}",
                        meta={"node_id": n.node_id, "status": n.status},
                    )
                )

    # --- Always pack open conflicts + missing evidence (surface without being asked) ---
    conflicts = list(
        (
            await session.execute(
                select(Conflict)
                .options(selectinload(Conflict.fact_links).selectinload(ConflictFact.fact))
                .where(
                    Conflict.case_id == case_id,
                    Conflict.tenant_id == tenant_id,
                    Conflict.status == "OPEN",
                )
            )
        )
        .scalars()
        .unique()
        .all()
    )
    gaps = list(
        (
            await session.execute(
                select(MissingEvidence).where(
                    MissingEvidence.case_id == case_id,
                    MissingEvidence.tenant_id == tenant_id,
                    MissingEvidence.status.in_(("NOT_PROVIDED", "OPEN")),
                )
            )
        )
        .scalars()
        .all()
    )
    open_conflicts = [
        {
            "conflict_id": c.conflict_id,
            "conflict_type": c.conflict_type,
            "summary": c.summary,
            "severity": c.severity,
        }
        for c in conflicts
    ]
    missing = [
        {
            "gap_id": g.gap_id,
            "gap_type": g.gap_type,
            "referenced_label": g.referenced_label,
            "required_doc_type": g.required_doc_type,
            "summary": g.summary,
        }
        for g in gaps
    ]
    if conflicts:
        notes["paths"].append("conflict_aware")
        for c in conflicts:
            hits.append(
                RetrievalHit(
                    kind="conflict",
                    score=0.9 if analysis.wants_conflicts else 0.55,
                    source="conflict_pack",
                    snippet=c.summary,
                    meta={"conflict_id": c.conflict_id, "conflict_type": c.conflict_type},
                )
            )
            for link in c.fact_links or []:
                if link.fact:
                    hits.extend(
                        _fact_hits(link.fact, evidence_by_id, score=0.85, source="conflict_pack")
                    )

    # Deduplicate + rank
    ranked = _dedupe_rank(hits)[:limit]
    return RetrievalPack(
        analysis=analysis,
        hits=ranked,
        open_conflicts=open_conflicts,
        missing_evidence=missing,
        notes=notes,
    )


def _fact_hits(
    fact: Fact,
    evidence_by_id: dict[uuid.UUID, EvidenceItem],
    *,
    score: float,
    source: str,
) -> list[RetrievalHit]:
    out: list[RetrievalHit] = [
        RetrievalHit(
            kind="fact",
            score=score,
            source=source,
            fact_id=fact.fact_id,
            document_id=fact.document_id,
            snippet=f"{fact.fact_type}: {fact.value_text or fact.value_normalized or '—'}",
            verification_state=fact.verification_state,
            meta={"predicate": fact.predicate, "fact_type": fact.fact_type},
        )
    ]
    for link in fact.evidence_links or []:
        ev = link.evidence_item or evidence_by_id.get(link.evidence_item_id)
        if not ev:
            continue
        out.append(
            RetrievalHit(
                kind="evidence",
                score=score * 0.95,
                source=source,
                fact_id=fact.fact_id,
                evidence_id=ev.evidence_id,
                document_id=ev.document_id,
                page=ev.page_number,
                snippet=(ev.text or "")[:400],
                bbox=ev.bbox,
                verification_state=fact.verification_state,
            )
        )
    return out


def _dedupe_rank(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    best: dict[str, RetrievalHit] = {}
    for h in hits:
        key = f"{h.kind}:{h.fact_id or ''}:{h.evidence_id or ''}:{h.snippet[:80]}"
        prev = best.get(key)
        if prev is None or h.score > prev.score:
            best[key] = h
    return sorted(best.values(), key=lambda x: x.score, reverse=True)
