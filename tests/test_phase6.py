"""Phase 6 domain engines: ownership chain, shares, legal findings, risk drivers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from packages.dd.area import reconcile_areas
from packages.dd.confidence import build_confidence_profiles
from packages.dd.gis import assess_gis_hooks
from packages.dd.legal import build_legal_findings_layer1, enrich_findings_layer2
from packages.dd.ownership import build_ownership_chain
from packages.dd.risk import RISK_WEIGHTS_V1, WEIGHTS_VERSION, assess_risk
from packages.dd.shares import account_shares
from packages.dd.survey import analyze_survey_identity


def _person(name: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), display_name=name)


def _event(
    *,
    event_type: str = "sale",
    event_date: datetime | None,
    parties: list[tuple[uuid.UUID, str]],
    shares: list[tuple[uuid.UUID | None, int, int]] | None = None,
    document_id: uuid.UUID | None = None,
    parcel_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        event_type=event_type,
        event_date=event_date,
        event_date_raw=None,
        registration_number=None,
        parcel_id=parcel_id,
        document_id=document_id or uuid.uuid4(),
        verification_state="EXTRACTED",
        parties=[
            SimpleNamespace(person_id=pid, role=role) for pid, role in parties
        ],
        shares=[
            SimpleNamespace(
                person_id=pid,
                share_numerator=num,
                share_denominator=den,
                share_text=f"{num}/{den}",
            )
            for pid, num, den in (shares or [])
        ],
    )


def _fact(**kwargs):
    defaults = dict(
        fact_id=f"fact_{uuid.uuid4().hex[:8]}",
        fact_type="encumbrance.mortgage",
        predicate="mortgage",
        value_text="HDFC Bank mortgage 2019",
        value_normalized="hdfc bank mortgage 2019",
        verification_state="EXTRACTED",
        confidence=0.9,
        document_id=uuid.uuid4(),
        unit=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _gap(**kwargs):
    defaults = dict(
        gap_id="gap_mort",
        gap_type="MORTGAGE_RELEASE_MISSING",
        referenced_label="Release deed (mortgage release)",
        required_doc_type="release_deed",
        summary="Mortgage is mentioned but no release deed was uploaded.",
        status="NOT_PROVIDED",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _conflict(**kwargs):
    defaults = dict(
        conflict_id="cnf_owner",
        conflict_type="OWNER_NAME_CONFLICT",
        severity="HIGH",
        status="OPEN",
        summary="OWNER_NAME_CONFLICT: Ram vs Shyam",
        fact_links=[],
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_mortgage_without_release_is_unresolved_not_clear():
    mortgage = _fact()
    gap = _gap()
    findings = build_legal_findings_layer1(
        facts=[mortgage],
        conflicts=[],
        gaps=[gap],
    )
    assert findings, "expected encumbrance finding"
    enc = next(f for f in findings if f.category == "ENCUMBRANCE")
    assert enc.status == "UNRESOLVED"
    assert "release" in " ".join(enc.missing_evidence).lower()
    assert "clear" not in enc.statement.lower() or "not clear" in enc.statement.lower()
    assert enc.details.get("clear_title_claimed") is False

    layer2 = await enrich_findings_layer2(findings)
    enc2 = next(f for f in layer2 if f.category == "ENCUMBRANCE")
    assert enc2.status == "UNRESOLVED"
    assert enc2.details.get("title_clear") is False
    assert enc2.details.get("layer2_route") == "pro"
    assert enc2.details.get("layer2_note")


def test_risk_panel_lists_unresolved_mortgage_and_owner_conflict():
    findings = build_legal_findings_layer1(
        facts=[_fact()],
        conflicts=[_conflict()],
        gaps=[_gap()],
    )
    risk = assess_risk(legal_findings=findings, conflicts=[_conflict()], gaps=[_gap()])
    codes = {d.code for d in risk.drivers}
    assert "UNRESOLVED_MORTGAGE" in codes
    assert "OWNER_IDENTITY_CONFLICT" in codes
    mortgage_driver = next(d for d in risk.drivers if d.code == "UNRESOLVED_MORTGAGE")
    assert mortgage_driver.weight == RISK_WEIGHTS_V1["UNRESOLVED_MORTGAGE"]
    assert mortgage_driver.weight == 30
    assert risk.weights_version == WEIGHTS_VERSION
    assert risk.risk_level in ("HIGH", "CRITICAL")
    assert "not a legal conclusion" in risk.disclaimer.lower()


def test_ownership_timeline_orders_events_and_flags_gap():
    a, b, c = _person("A"), _person("B"), _person("C")
    parcel = uuid.uuid4()
    e1 = _event(
        event_date=datetime(2001, 1, 1, tzinfo=timezone.utc),
        parties=[(a.id, "seller"), (b.id, "buyer")],
        parcel_id=parcel,
    )
    e2 = _event(
        event_date=datetime(2010, 6, 1, tzinfo=timezone.utc),
        parties=[(c.id, "seller"), (a.id, "buyer")],  # C sells but prior buyer was B
        parcel_id=parcel,
    )
    names = {str(a.id): "A", str(b.id): "B", str(c.id): "C"}
    chain = build_ownership_chain([e2, e1], person_names=names)
    assert len(chain.timeline) == 2
    assert chain.timeline[0].event_date < chain.timeline[1].event_date
    assert any(g["gap_type"] == "OWNERSHIP_CHAIN_GAP" for g in chain.gaps)
    assert chain.timeline[1].chain_status == "GAP"


def test_share_oversell_detected_deterministically():
    a, b, c = _person("A"), _person("B"), _person("C")
    e0 = _event(
        event_date=datetime(2000, 1, 1, tzinfo=timezone.utc),
        parties=[(a.id, "buyer")],
        shares=[(a.id, 1, 1)],
    )
    e1 = _event(
        event_date=datetime(2005, 1, 1, tzinfo=timezone.utc),
        parties=[(a.id, "seller"), (b.id, "buyer")],
        shares=[(b.id, 40, 100)],
    )
    e2 = _event(
        event_date=datetime(2008, 1, 1, tzinfo=timezone.utc),
        parties=[(a.id, "seller"), (c.id, "buyer")],
        shares=[(c.id, 70, 100)],  # A only holds 0.60
    )
    names = {str(a.id): "A", str(b.id): "B", str(c.id): "C"}
    result = account_shares([e0, e1, e2], person_names=names)
    assert any(c["conflict_type"] == "SHARE_OVERSELL_CONFLICT" for c in result.conflicts)


def test_area_reconciliation_flags_conflict():
    fa = _fact(
        fact_type="parcel.area",
        fact_id="fact_area_a",
        value_text="2.50 acre",
        value_normalized=str(2.50 * 4046.8564224),
    )
    fb = _fact(
        fact_type="parcel.area",
        fact_id="fact_area_b",
        value_text="1.00 acre",
        value_normalized=str(1.00 * 4046.8564224),
        document_id=uuid.uuid4(),
    )
    result = reconcile_areas([fa, fb])
    assert any(c["conflict_type"] == "AREA_CONFLICT" for c in result.conflicts)
    assert result.findings


def test_survey_ambiguity_not_verified():
    p1 = SimpleNamespace(
        id=uuid.uuid4(),
        display_label="Parcel 1",
        identifiers=[
            SimpleNamespace(id_type="survey_number", id_value="183/2", normalized_value="183/2")
        ],
    )
    p2 = SimpleNamespace(
        id=uuid.uuid4(),
        display_label="Parcel 2",
        identifiers=[
            SimpleNamespace(id_type="survey_number", id_value="183/2", normalized_value="183/2")
        ],
    )
    result = analyze_survey_identity([p1, p2])
    assert result.ambiguities
    assert all(a["mapping_status"] == "AMBIGUOUS" for a in result.ambiguities)
    assert any(f["identity_match"] == "AMBIGUOUS" for f in result.findings)


def test_gis_validity_not_equal_identity():
    gis = assess_gis_hooks(
        geo_payloads=[
            {
                "geometry_valid": True,
                "claimed_survey_no": "183/2",
                "matched_survey_no": "521",
                "boundary_consistent": "OK",
            }
        ]
    )
    assert gis.geometry_valid is True
    assert gis.identity_match == "MISMATCH"
    assert any(f["finding_type"] == "GEOMETRY_MISMATCH" for f in gis.findings)


def test_confidence_profiles_are_multi_dimension():
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    facts = [
        _fact(
            fact_type="party.owner",
            fact_id="fact_o1",
            value_text="Ram Kumar",
            value_normalized="ram kumar",
            document_id=doc_a,
            verification_state="CONFLICTING",
        ),
        _fact(
            fact_type="party.owner",
            fact_id="fact_o2",
            value_text="Shyam Kumar",
            value_normalized="shyam kumar",
            document_id=doc_b,
            verification_state="CONFLICTING",
        ),
    ]
    conflict = _conflict(
        fact_links=[
            SimpleNamespace(fact=facts[0]),
            SimpleNamespace(fact=facts[1]),
        ]
    )
    profiles = build_confidence_profiles(facts, conflicts=[conflict])
    assert len(profiles) == 2
    for p in profiles:
        d = p.to_dict()
        assert "extraction_confidence" in d
        assert "evidence_quality" in d
        assert "cross_document_agreement" in d
        assert d["cross_document_agreement"] == "CONFLICTING"
        assert "human_review_status" in d
        assert "external_verification" in d
