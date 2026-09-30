"""Phase 3 reconciliation: conflicts, missing evidence, graphs, scorecard."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from packages.domain.models import Fact, OwnershipEvent, OwnershipEventParty, Person
from packages.pipeline.reconciliation import (
    build_document_graph,
    compute_scorecard,
    detect_fact_conflicts,
    detect_missing_evidence,
    detect_ownership_sequence_conflicts,
)


def _fact(
    *,
    fact_type: str,
    value: str,
    document_id: uuid.UUID,
    state: str = "EXTRACTED",
) -> Fact:
    return Fact(
        id=uuid.uuid4(),
        fact_id=f"fact_{uuid.uuid4().hex[:12]}",
        tenant_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        document_id=document_id,
        fact_type=fact_type,
        predicate=fact_type.split(".")[-1],
        value_text=value,
        value_normalized=value.lower() if fact_type.startswith("party.") else value,
        verification_state=state,
        extraction_version="1.0",
    )


def test_two_deeds_owner_conflict_stays_open():
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    facts = [
        _fact(fact_type="party.owner", value="Sita Devi", document_id=doc_a),
        _fact(fact_type="party.owner", value="Lakshmi Devi", document_id=doc_b),
    ]
    conflicts = detect_fact_conflicts(facts)
    assert any(c.conflict_type == "OWNER_NAME_CONFLICT" for c in conflicts)
    assert all(c.severity == "HIGH" for c in conflicts if c.conflict_type == "OWNER_NAME_CONFLICT")
    # No silent winner — both facts linked
    owner = next(c for c in conflicts if c.conflict_type == "OWNER_NAME_CONFLICT")
    assert len(owner.fact_ids) == 2


def test_agreeing_owners_no_conflict():
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    facts = [
        _fact(fact_type="party.owner", value="Sita Devi", document_id=doc_a),
        _fact(fact_type="party.owner", value="Sita Devi", document_id=doc_b),
    ]
    assert detect_fact_conflicts(facts) == []


def test_area_conflict_beyond_tolerance():
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    fa = _fact(fact_type="parcel.area", value="2023.4", document_id=doc_a)
    fa.value_normalized = "2023.4"
    fb = _fact(fact_type="parcel.area", value="4046.8", document_id=doc_b)
    fb.value_normalized = "4046.8"
    conflicts = detect_fact_conflicts([fa, fb])
    assert any(c.conflict_type == "AREA_CONFLICT" for c in conflicts)


def test_missing_prior_deed_from_reference_fact():
    doc_id = uuid.uuid4()
    fact = _fact(fact_type="document.reference", value="Sale Deed 2005", document_id=doc_id)
    fact.value_json = {"doc_type": "sale_deed", "year": "2005"}

    class Doc:
        def __init__(self):
            self.id = doc_id
            self.source_filename = "sale_deed_2020.pdf"
            self.content_hash = "abc"

    gaps = detect_missing_evidence(
        facts=[fact],
        evidence_items=[],
        documents=[Doc()],
        classifications={doc_id: "sale_deed"},
    )
    assert any(g.gap_type == "REFERENCED_INSTRUMENT_MISSING" for g in gaps)
    assert any("2005" in g.referenced_label for g in gaps)


def test_document_graph_marks_missing_reference():
    doc_id = uuid.uuid4()

    class Doc:
        def __init__(self):
            self.id = doc_id
            self.source_filename = "sale_deed_2020.pdf"
            self.content_hash = "abc"

    from packages.pipeline.reconciliation import DetectedGap

    gaps = [
        DetectedGap(
            gap_type="REFERENCED_INSTRUMENT_MISSING",
            referenced_label="Sale Deed 2005",
            required_doc_type="sale_deed",
            summary="missing",
            referenced_from_document_id=doc_id,
        )
    ]
    graph = build_document_graph(
        documents=[Doc()],
        classifications={doc_id: "sale_deed"},
        gaps=gaps,
    )
    statuses = {n["status"] for n in graph.nodes}
    assert "PROVIDED" in statuses
    assert "REFERENCED_BUT_MISSING" in statuses
    assert any(e["edge_type"] == "references" for e in graph.edges)


def test_ownership_sequence_conflict():
    case_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    p_buyer = Person(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        case_id=case_id,
        display_name="A",
        normalized_name="a",
    )
    p_other = Person(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        case_id=case_id,
        display_name="B",
        normalized_name="b",
    )
    earlier = OwnershipEvent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        case_id=case_id,
        document_id=uuid.uuid4(),
        event_type="sale",
        event_date=datetime(2005, 6, 15, tzinfo=timezone.utc),
        event_date_raw="15/06/2005",
        verification_state="EXTRACTED",
    )
    earlier.parties = [
        OwnershipEventParty(
            id=uuid.uuid4(),
            ownership_event_id=earlier.id,
            person_id=p_buyer.id,
            role="buyer",
        )
    ]
    later = OwnershipEvent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        case_id=case_id,
        document_id=uuid.uuid4(),
        event_type="sale",
        event_date=datetime(2020, 3, 12, tzinfo=timezone.utc),
        event_date_raw="12/03/2020",
        verification_state="EXTRACTED",
    )
    later.parties = [
        OwnershipEventParty(
            id=uuid.uuid4(),
            ownership_event_id=later.id,
            person_id=p_other.id,
            role="seller",
        )
    ]
    conflicts = detect_ownership_sequence_conflicts([earlier, later])
    assert any(c.conflict_type == "OWNERSHIP_SEQUENCE_CONFLICT" for c in conflicts)


def test_scorecard_surfaces_conflicts():
    dims = compute_scorecard(
        documents=[],
        pages=[],
        integrity=[],
        evidence_items=[],
        facts=[],
        events=[],
        open_conflicts=2,
        missing_count=1,
        classifications={},
    )
    assert dims["file_integrity"] == "INCOMPLETE"
    assert dims["ownership_evidence"] == "INCOMPLETE"
    assert dims["gis"] == "NOT_PROVIDED"


@pytest.mark.asyncio
async def test_mock_extract_detects_prior_deed_reference():
    from packages.ai.mock_provider import MockProvider

    provider = MockProvider()
    result = await provider.extract_structured(
        doc_type="sale_deed",
        filename="sale_deed_2020.pdf",
        evidence_pages=[
            {
                "page_number": 1,
                "text": (
                    "Sale Deed dated 12/03/2020 Seller Ram Kumar Buyer Sita Devi "
                    "Gata No. 183/2 prior sale deed 2005 Area 0.5 acre"
                ),
                "evidence_id": "ev_ref",
            }
        ],
    )
    assert any(f.fact_type == "document.reference" for f in result.facts)
    assert result.referenced_documents
