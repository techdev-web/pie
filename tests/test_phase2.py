"""Unit tests for Phase 2 normalization + mock structured extract."""

from __future__ import annotations

import pytest

from packages.ai.mock_provider import MockProvider
from packages.pipeline.normalization import (
    normalize_identifier,
    normalize_person_name,
    parse_area,
    parse_date,
    parse_share,
    validate_candidate_fact,
)


def test_normalize_identifier():
    assert normalize_identifier("Gata No. 183/2") == "183/2"
    assert normalize_identifier("KH-102") == "kh-102"
    assert normalize_identifier(" 183 / 2 ") == "183/2"


def test_normalize_person_name():
    assert normalize_person_name("Shri Ram Kumar") == "ram kumar"
    assert normalize_person_name("  SITA   DEVI ") == "sita devi"


def test_parse_date_dmy():
    p = parse_date("12/03/2020")
    assert p.parsed is not None
    assert p.iso == "2020-03-12"
    assert p.ambiguous is True  # day and month both <= 12


def test_parse_date_ymd():
    p = parse_date("2020-03-12")
    assert p.iso == "2020-03-12"
    assert p.ambiguous is False


def test_parse_area_acre():
    a = parse_area("0.5 acre")
    assert a.value == 0.5
    assert a.sqm is not None
    assert abs(a.sqm - 2023.428) < 1.0


def test_parse_share():
    num, den, text = parse_share("1/2")
    assert (num, den) == (1, 2)
    assert text == "1/2"


def test_validate_candidate_requires_value_unless_not_found():
    errs = validate_candidate_fact(
        {"fact_type": "party.seller", "predicate": "seller_name", "verification_state": "EXTRACTED"}
    )
    assert errs
    ok = validate_candidate_fact(
        {
            "fact_type": "encumbrance.mortgage",
            "predicate": "mortgage_status",
            "verification_state": "NOT_FOUND",
        }
    )
    assert ok == []


@pytest.mark.asyncio
async def test_mock_structured_extract_sale_deed():
    provider = MockProvider()
    result = await provider.extract_structured(
        doc_type="sale_deed",
        filename="sale_deed.pdf",
        evidence_pages=[
            {
                "page_number": 1,
                "text": (
                    "Gata No. 183/2 Village Example Seller Ram Kumar Buyer Sita Devi "
                    "Sale Deed dated 12/03/2020 Registration No. REG-1234 Area 0.5 acre"
                ),
                "evidence_id": "ev_test",
            }
        ],
    )
    types = {f.fact_type for f in result.facts}
    assert "party.seller" in types
    assert "party.buyer" in types
    assert "parcel.survey_number" in types
    assert "transaction.date" in types
    assert any(p.name == "Ram Kumar" for p in result.persons)
    assert result.parcels and result.parcels[0].identifiers
    assert result.ownership_events
