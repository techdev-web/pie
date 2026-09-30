"""Case chat: retrieval → conflict-aware packing → synthesis → answer contract."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.ai.provider import get_llm_provider
from packages.domain.models import (
    Conversation,
    ConversationMessage,
    RetrievalTrace,
)
from packages.observability import get_logger
from packages.retrieval.guardrails import apply_guardrails
from packages.retrieval.hybrid import RetrievalHit, RetrievalPack, hybrid_retrieve
from packages.retrieval.memory import get_latest_memory

log = get_logger("retrieval.chat")

CHAT_SYSTEM = """You are PIE, an evidence-first due diligence assistant.
Rules:
1. Never invent identifiers, dates, or owners.
2. Never resolve conflicts without evidence — report them.
3. Never convert "not found in uploads" into "does not exist".
4. Cite evidence_id / page for material claims.
5. Separate document statements from conclusions.
6. Ask for additional documents when required.
Return ONLY valid JSON:
{"answer": str, "status": "SUPPORTED|PARTIALLY_SUPPORTED|CONFLICTING|INSUFFICIENT_EVIDENCE|NOT_FOUND|REQUIRES_REVIEW", "open_questions": [str]}
"""


async def answer_case_question(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    message: str,
    conversation_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    pack = await hybrid_retrieve(
        session, case_id=case_id, tenant_id=tenant_id, query=message
    )
    memory = await get_latest_memory(session, case_id=case_id, tenant_id=tenant_id)

    # Deterministic synthesis for core query classes (truth layer, not raw OCR alone)
    draft = _deterministic_answer(pack, memory_summary=memory.summary_text if memory else None)
    if draft is None:
        draft = await _llm_synthesize(pack, message=message, memory=memory)

    evidence_out = _evidence_contract(pack.hits)
    snippets = [e["snippet"] for e in evidence_out]
    fact_values = [
        h.snippet
        for h in pack.hits
        if h.kind == "fact" and h.verification_state
    ]
    answer, status, warnings = apply_guardrails(
        answer=draft["answer"],
        status=draft["status"],
        evidence_snippets=snippets,
        fact_values=fact_values,
        open_conflicts=pack.open_conflicts,
    )
    open_questions = list(draft.get("open_questions") or [])
    for q in pack.missing_evidence[:5]:
        label = q.get("referenced_label")
        if label and label not in open_questions:
            open_questions.append(f"Upload: {label}")

    contract = {
        "answer": answer,
        "status": status,
        "evidence": evidence_out,
        "conflicts": pack.open_conflicts,
        "missing_evidence": pack.missing_evidence,
        "open_questions": open_questions,
        "guardrail_warnings": warnings,
        "query_class": pack.analysis.query_class,
        "extracted_ids": pack.analysis.extracted_ids,
        "retrieval_paths": pack.notes.get("paths", []),
    }

    # Persist conversation + trace
    conv = await _ensure_conversation(
        session,
        case_id=case_id,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        title=(message[:80] if message else "Case chat"),
    )
    trace = RetrievalTrace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        case_id=case_id,
        query=message,
        query_class=pack.analysis.query_class,
        extracted_ids=pack.analysis.extracted_ids,
        retrieved=[
            {
                "kind": h.kind,
                "score": h.score,
                "source": h.source,
                "fact_id": h.fact_id,
                "evidence_id": h.evidence_id,
                "page": h.page,
            }
            for h in pack.hits[:30]
        ],
        packing_notes={**pack.notes, "guardrail_warnings": warnings},
    )
    session.add(trace)
    await session.flush()

    session.add(
        ConversationMessage(
            id=uuid.uuid4(),
            conversation_id=conv.id,
            tenant_id=tenant_id,
            case_id=case_id,
            role="user",
            content=message,
        )
    )
    assistant_msg = ConversationMessage(
        id=uuid.uuid4(),
        conversation_id=conv.id,
        tenant_id=tenant_id,
        case_id=case_id,
        role="assistant",
        content=answer,
        answer_status=status,
        retrieval_trace_id=trace.id,
        answer_payload=contract,
    )
    session.add(assistant_msg)
    conv.updated_at = datetime.now(timezone.utc)
    await session.commit()

    contract["conversation_id"] = str(conv.id)
    contract["message_id"] = str(assistant_msg.id)
    contract["retrieval_trace_id"] = str(trace.id)
    log.info(
        "chat_answered",
        case_id=str(case_id),
        status=status,
        query_class=pack.analysis.query_class,
    )
    return contract


async def _ensure_conversation(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    title: str,
) -> Conversation:
    if conversation_id is not None:
        conv = await session.get(Conversation, conversation_id)
        if conv and conv.case_id == case_id and conv.tenant_id == tenant_id:
            return conv
    conv = Conversation(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        case_id=case_id,
        title=title,
    )
    session.add(conv)
    await session.flush()
    return conv


def _evidence_contract(hits: list[RetrievalHit]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for h in hits:
        if h.kind != "evidence" or not h.evidence_id:
            continue
        if h.evidence_id in seen:
            continue
        seen.add(h.evidence_id)
        out.append(
            {
                "document_id": str(h.document_id) if h.document_id else None,
                "page": h.page,
                "snippet": h.snippet,
                "bbox": h.bbox or [],
                "evidence_id": h.evidence_id,
            }
        )
        if len(out) >= 12:
            break
    # If no evidence hits, include fact snippets as weak citations without inventing pages
    if not out:
        for h in hits:
            if h.kind == "fact" and h.fact_id:
                out.append(
                    {
                        "document_id": str(h.document_id) if h.document_id else None,
                        "page": h.page,
                        "snippet": h.snippet,
                        "bbox": [],
                        "evidence_id": None,
                        "fact_id": h.fact_id,
                    }
                )
                if len(out) >= 8:
                    break
    return out


def _deterministic_answer(
    pack: RetrievalPack, *, memory_summary: str | None
) -> dict[str, Any] | None:
    qclass = pack.analysis.query_class

    if qclass == "upload_next":
        if not pack.missing_evidence:
            return {
                "answer": (
                    "No missing-evidence gaps are currently recorded for this case. "
                    "If you have prior deeds, mutations, EC, or release deeds, upload them anyway — "
                    "absence from the gap list is not a clearance."
                ),
                "status": "SUPPORTED",
                "open_questions": [],
            }
        lines = ["Based on the missing-evidence graph, upload next:"]
        for g in pack.missing_evidence[:8]:
            lines.append(f"- {g.get('referenced_label')} ({g.get('gap_type')})")
        if memory_summary:
            lines.append(f"\nCase memory: {memory_summary}")
        return {
            "answer": "\n".join(lines),
            "status": "PARTIALLY_SUPPORTED",
            "open_questions": [g.get("referenced_label") for g in pack.missing_evidence[:5]],
        }

    if qclass == "conflicts":
        if not pack.open_conflicts:
            return {
                "answer": "No OPEN conflicts are recorded on this case right now.",
                "status": "SUPPORTED",
                "open_questions": [],
            }
        lines = ["Open conflicts (not resolved — both sides retained):"]
        for c in pack.open_conflicts:
            lines.append(f"- [{c.get('conflict_type')}] {c.get('summary')}")
        return {
            "answer": "\n".join(lines),
            "status": "CONFLICTING",
            "open_questions": [c.get("summary") for c in pack.open_conflicts[:5]],
        }

    if qclass == "ownership":
        owner_hits = [
            h
            for h in pack.hits
            if h.kind == "fact"
            and h.meta.get("fact_type") in ("party.owner", "party.buyer", "party.seller")
        ]
        if not owner_hits and not any(
            h.kind == "fact" and "owner" in (h.snippet or "").lower() for h in pack.hits
        ):
            return {
                "answer": (
                    "Insufficient evidence in the case knowledge layer to identify a current owner. "
                    "Upload a sale deed or ownership instrument."
                ),
                "status": "INSUFFICIENT_EVIDENCE",
                "open_questions": ["Upload ownership deed"],
            }

        # Prefer VERIFIED owners
        owners = [h for h in owner_hits if h.meta.get("fact_type") == "party.owner"]
        if not owners:
            owners = [h for h in owner_hits if h.meta.get("fact_type") == "party.buyer"]
        if not owners:
            owners = owner_hits

        verified = [h for h in owners if h.verification_state == "VERIFIED"]
        conflicting = [h for h in owners if h.verification_state == "CONFLICTING"]
        ranked = verified or owners

        if conflicting or pack.open_conflicts:
            vals = sorted({_fact_value(h) for h in owners if _fact_value(h)})
            answer = (
                "Ownership is CONFLICTING across documents; PIE does not pick a winner.\n"
                + "\n".join(f"- {v}" for v in vals)
                + "\nReview cited evidence and resolve via confirmation or additional documents."
            )
            return {
                "answer": answer,
                "status": "CONFLICTING",
                "open_questions": [c.get("summary") for c in pack.open_conflicts[:3]],
            }

        best = ranked[0]
        state = best.verification_state or "EXTRACTED"
        value = _fact_value(best)
        answer = f"Current owner (from case facts, state={state}): {value}."
        if state == "VERIFIED":
            status = "SUPPORTED"
        elif state in ("CORROBORATED", "SUPPORTED"):
            status = "SUPPORTED"
        else:
            status = "PARTIALLY_SUPPORTED"
        if pack.missing_evidence:
            answer += (
                "\nNote: missing evidence remains on this case — "
                "owner finding is limited to uploaded documents."
            )
            status = "PARTIALLY_SUPPORTED" if status == "SUPPORTED" else status
        return {"answer": answer, "status": status, "open_questions": []}

    if qclass == "identifier":
        ids = pack.analysis.extracted_ids
        id_labels = ", ".join(i["normalized"] for i in ids)
        exact = [h for h in pack.hits if h.source == "exact_id"]
        if not exact:
            return {
                "answer": (
                    f"No exact matches found in the case knowledge layer for identifier(s): {id_labels}. "
                    "This means not found in uploaded documents — not that the parcel/instrument does not exist."
                ),
                "status": "NOT_FOUND",
                "open_questions": [f"Upload document covering {id_labels}"],
            }
        lines = [f"Exact identifier search for {id_labels} (ID path before semantic):"]
        for h in exact[:10]:
            cite = h.evidence_id or h.fact_id or ""
            page = f" p.{h.page}" if h.page else ""
            lines.append(f"- [{h.source}]{page} {h.snippet[:200]} {cite}")
        status = "SUPPORTED" if any(h.kind == "evidence" for h in exact) else "PARTIALLY_SUPPORTED"
        if pack.open_conflicts:
            status = "CONFLICTING" if any(
                "SURVEY" in (c.get("conflict_type") or "") for c in pack.open_conflicts
            ) else status
        return {"answer": "\n".join(lines), "status": status, "open_questions": []}

    return None


def _fact_value(hit: RetrievalHit) -> str:
    # snippet like "party.owner: Sita Devi"
    sn = hit.snippet or ""
    if ": " in sn:
        return sn.split(": ", 1)[1].strip()
    return sn.strip()


async def _llm_synthesize(
    pack: RetrievalPack, *, message: str, memory: Any
) -> dict[str, Any]:
    provider = get_llm_provider()
    # Prefer provider.chat_synthesize if present; else build from packed context deterministically
    packed = {
        "query": message,
        "query_class": pack.analysis.query_class,
        "hits": [
            {
                "kind": h.kind,
                "source": h.source,
                "score": round(h.score, 3),
                "snippet": h.snippet,
                "evidence_id": h.evidence_id,
                "fact_id": h.fact_id,
                "page": h.page,
                "verification_state": h.verification_state,
            }
            for h in pack.hits[:20]
        ],
        "conflicts": pack.open_conflicts,
        "missing_evidence": pack.missing_evidence,
        "memory_summary": getattr(memory, "summary_text", None),
    }
    synthesize = getattr(provider, "synthesize_answer", None)
    if synthesize is not None:
        try:
            result = await synthesize(system=CHAT_SYSTEM, packed_context=packed)
            if isinstance(result, dict) and result.get("answer"):
                return {
                    "answer": str(result["answer"]),
                    "status": str(result.get("status") or "PARTIALLY_SUPPORTED"),
                    "open_questions": list(result.get("open_questions") or []),
                }
        except Exception as exc:
            log.warning("llm_synthesize_failed", error=str(exc))

    # Fallback: stitch from top hits without inventing
    if not pack.hits:
        return {
            "answer": (
                "Insufficient evidence in the case knowledge layer to answer. "
                "Upload relevant documents or refine the question."
            ),
            "status": "INSUFFICIENT_EVIDENCE",
            "open_questions": ["Provide more documents"],
        }
    lines = ["From the case knowledge layer (facts/evidence/conflicts):"]
    for h in pack.hits[:8]:
        cite = h.evidence_id or h.fact_id or h.kind
        page = f" p.{h.page}" if h.page else ""
        state = f" [{h.verification_state}]" if h.verification_state else ""
        lines.append(f"- {h.snippet[:240]}{state}{page} ({cite})")
    if pack.open_conflicts:
        lines.append("Open conflicts:")
        for c in pack.open_conflicts[:3]:
            lines.append(f"- {c.get('summary')}")
    status = "PARTIALLY_SUPPORTED"
    if pack.open_conflicts:
        status = "CONFLICTING"
    return {"answer": "\n".join(lines), "status": status, "open_questions": []}
