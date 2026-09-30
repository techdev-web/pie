"""Embedding index for evidence + facts (JSONB vectors; cosine in Python)."""

from __future__ import annotations

import math
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.ai.provider import get_llm_provider
from packages.domain.models import Embedding, EvidenceItem, Fact
from packages.domain.textutil import normalize_text
from packages.observability import get_logger

log = get_logger("retrieval.embeddings")

EMBEDDING_MODEL = "mock-or-gemini-embed"
BATCH = 32


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def index_case_embeddings(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Upsert embeddings for evidence and facts in a case (optionally one document)."""
    provider = get_llm_provider()

    ev_q = select(EvidenceItem).where(
        EvidenceItem.case_id == case_id, EvidenceItem.tenant_id == tenant_id
    )
    fact_q = select(Fact).where(Fact.case_id == case_id, Fact.tenant_id == tenant_id)
    if document_id is not None:
        ev_q = ev_q.where(EvidenceItem.document_id == document_id)
        fact_q = fact_q.where(Fact.document_id == document_id)

    evidence = list((await session.execute(ev_q)).scalars().all())
    facts = list((await session.execute(fact_q)).scalars().all())

    existing = list(
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
    by_key = {(e.source_type, e.source_id): e for e in existing}

    items: list[tuple[str, uuid.UUID, uuid.UUID | None, str]] = []
    for ev in evidence:
        text = (ev.text or "").strip()
        if not text:
            continue
        items.append(("evidence", ev.id, ev.document_id, text[:4000]))
    for fact in facts:
        parts = [
            fact.fact_type,
            fact.predicate or "",
            fact.value_text or "",
            fact.value_normalized or "",
            fact.verification_state,
        ]
        text = " | ".join(p for p in parts if p).strip()
        if not text:
            continue
        items.append(("fact", fact.id, fact.document_id, text[:2000]))

    created = 0
    updated = 0
    for i in range(0, len(items), BATCH):
        batch = items[i : i + BATCH]
        to_embed = [t for *_, t in batch]
        vectors = await provider.embed(to_embed)
        for (source_type, source_id, doc_id, text), vec in zip(batch, vectors):
            key = (source_type, source_id)
            norm = normalize_text(text)
            if key in by_key:
                row = by_key[key]
                row.content_text = text
                row.content_normalized = norm
                row.embedding = list(vec)
                row.dims = len(vec)
                row.document_id = doc_id
                updated += 1
            else:
                row = Embedding(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    case_id=case_id,
                    source_type=source_type,
                    source_id=source_id,
                    document_id=doc_id,
                    content_text=text,
                    content_normalized=norm,
                    embedding=list(vec),
                    dims=len(vec),
                    model=EMBEDDING_MODEL,
                )
                session.add(row)
                by_key[key] = row
                created += 1

    await session.commit()
    log.info(
        "embeddings_indexed",
        case_id=str(case_id),
        created=created,
        updated=updated,
        evidence=len(evidence),
        facts=len(facts),
    )
    return {"created": created, "updated": updated, "total": len(by_key)}
