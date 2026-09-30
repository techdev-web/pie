"""Phase 4: hybrid RAG, exact ID search, answer contract, memory writebacks."""

from __future__ import annotations

import uuid

from packages.retrieval.guardrails import apply_guardrails, asserted_identifiers
from packages.retrieval.hybrid import RetrievalHit, RetrievalPack, _dedupe_rank
from packages.retrieval.query import classify_query, extract_identifiers
from packages.retrieval.chat import _deterministic_answer


def test_extract_survey_identifier():
    ids = extract_identifiers("What about survey 183/2?")
    assert any(i["normalized"] == "183/2" for i in ids)


def test_classify_owner_query():
    a = classify_query("Who is the current owner?")
    assert a.query_class == "ownership"
    assert a.wants_owner


def test_classify_identifier_hits_exact_path_first():
    a = classify_query("What about survey 183/2?")
    assert a.query_class == "identifier"
    assert a.extracted_ids
    assert a.extracted_ids[0]["normalized"] == "183/2"


def test_classify_upload_next():
    a = classify_query("What should I upload next?")
    assert a.query_class == "upload_next"


def test_owner_answer_contract_with_citations():
    doc_id = uuid.uuid4()
    pack = RetrievalPack(
        analysis=classify_query("Who is the current owner?"),
        hits=[
            RetrievalHit(
                kind="fact",
                score=1.0,
                source="entity",
                fact_id="fact_owner",
                document_id=doc_id,
                snippet="party.owner: Sita Devi",
                verification_state="SUPPORTED",
                meta={"fact_type": "party.owner", "predicate": "owner_name"},
            ),
            RetrievalHit(
                kind="evidence",
                score=0.95,
                source="entity",
                fact_id="fact_owner",
                evidence_id="ev_owner1",
                document_id=doc_id,
                page=14,
                snippet="Buyer Sita Devi",
                bbox=[10, 20, 100, 40],
                verification_state="SUPPORTED",
            ),
        ],
        open_conflicts=[],
        missing_evidence=[],
    )
    draft = _deterministic_answer(pack, memory_summary=None)
    assert draft is not None
    assert "Sita Devi" in draft["answer"]
    assert draft["status"] in ("SUPPORTED", "PARTIALLY_SUPPORTED")


def test_verified_owner_preferred_in_chat():
    pack = RetrievalPack(
        analysis=classify_query("Who is the current owner?"),
        hits=[
            RetrievalHit(
                kind="fact",
                score=0.85,
                source="entity",
                fact_id="fact_a",
                snippet="party.owner: Old Name",
                verification_state="SUPPORTED",
                meta={"fact_type": "party.owner"},
            ),
            RetrievalHit(
                kind="fact",
                score=1.0,
                source="entity",
                fact_id="fact_b",
                snippet="party.owner: Sita Devi",
                verification_state="VERIFIED",
                meta={"fact_type": "party.owner"},
            ),
        ],
        open_conflicts=[],
        missing_evidence=[],
    )
    draft = _deterministic_answer(pack, memory_summary=None)
    assert draft is not None
    assert "Sita Devi" in draft["answer"]
    assert "VERIFIED" in draft["answer"]
    assert draft["status"] == "SUPPORTED"


def test_identifier_query_uses_exact_id_hits():
    pack = RetrievalPack(
        analysis=classify_query("What about survey 183/2?"),
        hits=[
            RetrievalHit(
                kind="evidence",
                score=0.98,
                source="exact_id",
                evidence_id="ev_survey",
                page=2,
                snippet="Gata No. 183/2 Village Example",
            ),
            RetrievalHit(
                kind="evidence",
                score=0.4,
                source="vector",
                evidence_id="ev_other",
                page=9,
                snippet="unrelated text about rainfall",
            ),
        ],
        open_conflicts=[],
        missing_evidence=[],
    )
    draft = _deterministic_answer(pack, memory_summary=None)
    assert draft is not None
    assert "exact" in draft["answer"].lower() or "183/2" in draft["answer"]
    assert draft["status"] in ("SUPPORTED", "PARTIALLY_SUPPORTED", "CONFLICTING")


def test_guardrail_downgrades_invented_identifier():
    answer, status, warnings = apply_guardrails(
        answer="The survey number is 999/9 according to analysis.",
        status="SUPPORTED",
        evidence_snippets=["Gata No. 183/2 Village Example"],
        fact_values=["183/2"],
        open_conflicts=[],
    )
    assert status == "INSUFFICIENT_EVIDENCE"
    assert warnings
    assert "999/9" in asserted_identifiers("survey 999/9")


def test_upload_next_from_missing_evidence():
    pack = RetrievalPack(
        analysis=classify_query("What should I upload next?"),
        hits=[],
        open_conflicts=[],
        missing_evidence=[
            {
                "gap_id": "gap_1",
                "gap_type": "REFERENCED_INSTRUMENT_MISSING",
                "referenced_label": "Sale Deed 2005",
                "required_doc_type": "sale_deed",
                "summary": "Prior deed referenced",
            }
        ],
    )
    draft = _deterministic_answer(pack, memory_summary="Case memory v1.")
    assert draft is not None
    assert "Sale Deed 2005" in draft["answer"]


def test_dedupe_rank_keeps_highest_score():
    hits = [
        RetrievalHit(kind="fact", score=0.5, source="keyword", fact_id="f1", snippet="a"),
        RetrievalHit(kind="fact", score=0.9, source="exact_id", fact_id="f1", snippet="a"),
    ]
    ranked = _dedupe_rank(hits)
    assert len(ranked) == 1
    assert ranked[0].score == 0.9
