"""Deterministic mock LLM for local/CI without Gemini."""

from __future__ import annotations

from typing import Any

from packages.ai.provider import ClassificationResult, PageExtractResult
from packages.ai.schemas import (
    CandidateFactPayload,
    EncumbrancePayload,
    OwnershipEventPayload,
    ParcelPayload,
    PersonPayload,
    StructuredExtractResult,
)


class MockProvider:
    async def classify(self, *, filename: str | None, sample_text: str) -> ClassificationResult:
        name = (filename or "").lower()
        doc_type = "unknown"
        if "sale" in name or "deed" in name:
            doc_type = "sale_deed"
        elif "khata" in name:
            doc_type = "khatauni"
        elif "mutation" in name:
            doc_type = "mutation"
        elif sample_text and "sale deed" in sample_text.lower():
            doc_type = "sale_deed"
        return ClassificationResult(
            doc_type=doc_type,
            confidence=0.85,
            details={"reason": "mock_heuristic"},
            model="mock",
        )

    async def extract_page(
        self,
        *,
        page_number: int,
        text_layer: str | None,
        image_bytes: bytes | None,
        force_vision: bool = False,
    ) -> PageExtractResult:
        if text_layer and text_layer.strip() and not force_vision:
            return PageExtractResult(
                text=text_layer.strip(),
                confidence=0.95,
                provider="digital_text",
                source_type="DIGITAL",
                model="mock",
            )
        mock_text = (
            f"[mock OCR page {page_number}] "
            "Gata No. 183/2 Village Example Seller Ram Kumar Buyer Sita Devi "
            "Sale Deed dated 12/03/2020 Registration No. REG-1234 "
            "Area 0.5 acre"
        )
        return PageExtractResult(
            text=mock_text,
            confidence=0.8,
            provider="mock_vision",
            source_type="OCR",
            bbox=[0, 0, 100, 100],
            model="mock",
        )

    async def extract_structured(
        self,
        *,
        doc_type: str,
        filename: str | None,
        evidence_pages: list[dict[str, Any]],
    ) -> StructuredExtractResult:
        combined = "\n".join(
            f"Page {p.get('page_number')}: {p.get('text', '')}" for p in evidence_pages
        ).lower()
        page_num = evidence_pages[0]["page_number"] if evidence_pages else 1
        snippet = (evidence_pages[0].get("text") or "")[:240] if evidence_pages else ""

        # Prefer sale-deed style extraction when text looks like a deed or mock OCR
        is_sale = (
            doc_type == "sale_deed"
            or "sale deed" in combined
            or "seller" in combined
            or "buyer" in combined
            or "gata" in combined
        )

        if not is_sale and not combined.strip():
            return StructuredExtractResult(
                facts=[
                    CandidateFactPayload(
                        fact_type="party.seller",
                        predicate="seller_name",
                        verification_state="NOT_FOUND",
                        confidence=0.4,
                        attributes={"scope": "supplied_documents"},
                    )
                ],
                model="mock",
            )

        seller = "Ram Kumar" if "ram kumar" in combined else "Unknown Seller"
        buyer = "Sita Devi" if "sita devi" in combined else "Unknown Buyer"
        survey = "183/2" if "183/2" in combined else None
        date_raw = "12/03/2020" if "12/03/2020" in combined or "12/03/2020" in combined else None
        if "dated" in combined and not date_raw:
            date_raw = "12/03/2020"
        reg = "REG-1234" if "reg-1234" in combined else None
        area = "0.5 acre" if "0.5 acre" in combined or "acre" in combined else None

        persons = [
            PersonPayload(name=seller, role="seller"),
            PersonPayload(name=buyer, role="buyer"),
        ]
        parcels = []
        if survey:
            parcels.append(
                ParcelPayload(
                    label=f"Gata {survey}",
                    identifiers=[{"id_type": "gata", "id_value": survey}],
                    area_raw=area,
                    village="Example" if "village example" in combined else None,
                )
            )

        events = [
            OwnershipEventPayload(
                event_type="sale",
                event_date_raw=date_raw or "12/03/2020",
                registration_number=reg or "REG-1234",
                seller_names=[seller],
                buyer_names=[buyer],
                consideration_raw=None,
                share_text="1/1",
            )
        ]

        facts = [
            CandidateFactPayload(
                fact_type="party.seller",
                predicate="seller_name",
                value_text=seller,
                page_number=page_num,
                evidence_snippet=snippet or seller,
                confidence=0.9,
            ),
            CandidateFactPayload(
                fact_type="party.buyer",
                predicate="buyer_name",
                value_text=buyer,
                page_number=page_num,
                evidence_snippet=snippet or buyer,
                confidence=0.9,
            ),
            CandidateFactPayload(
                fact_type="party.owner",
                predicate="owner_name",
                value_text=buyer,
                page_number=page_num,
                evidence_snippet=snippet or buyer,
                confidence=0.85,
                attributes={"as_of": "post_transfer"},
            ),
        ]
        if survey:
            facts.append(
                CandidateFactPayload(
                    fact_type="parcel.survey_number",
                    predicate="survey_or_gata",
                    value_text=survey,
                    page_number=page_num,
                    evidence_snippet=snippet or survey,
                    confidence=0.92,
                )
            )
        if area:
            facts.append(
                CandidateFactPayload(
                    fact_type="parcel.area",
                    predicate="area",
                    value_text=area,
                    page_number=page_num,
                    evidence_snippet=snippet or area,
                    confidence=0.8,
                )
            )
        facts.append(
            CandidateFactPayload(
                fact_type="transaction.date",
                predicate="deed_date",
                value_text=date_raw or "12/03/2020",
                page_number=page_num,
                evidence_snippet=snippet or (date_raw or ""),
                confidence=0.88,
            )
        )
        facts.append(
            CandidateFactPayload(
                fact_type="transaction.registration_number",
                predicate="registration_number",
                value_text=reg or "REG-1234",
                page_number=page_num,
                evidence_snippet=snippet or (reg or ""),
                confidence=0.87,
            )
        )

        encumbrances: list[EncumbrancePayload] = []
        if "mortgage" in combined:
            encumbrances.append(
                EncumbrancePayload(
                    encumbrance_type="mortgage",
                    status="mentioned",
                    page_number=page_num,
                    evidence_snippet=snippet,
                    confidence=0.7,
                )
            )
            facts.append(
                CandidateFactPayload(
                    fact_type="encumbrance.mortgage",
                    predicate="mortgage_status",
                    value_text="mentioned",
                    page_number=page_num,
                    evidence_snippet=snippet,
                    confidence=0.7,
                    verification_state="REQUIRES_REVIEW",
                )
            )
        else:
            facts.append(
                CandidateFactPayload(
                    fact_type="encumbrance.mortgage",
                    predicate="mortgage_status",
                    verification_state="NOT_FOUND",
                    confidence=0.5,
                    attributes={"scope": "supplied_documents"},
                )
            )

        return StructuredExtractResult(
            persons=persons,
            parcels=parcels,
            ownership_events=events,
            encumbrances=encumbrances,
            facts=facts,
            model="mock",
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t) % 97) / 97.0] * 8 for t in texts]
