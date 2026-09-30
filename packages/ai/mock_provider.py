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
    ReferencedDocumentPayload,
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
        pass_id: str = "ocr_a",
    ) -> PageExtractResult:
        from packages.pipeline.ocr_dual import diverge_survey_in_text

        if text_layer and text_layer.strip() and not force_vision and pass_id != "ocr_b":
            return PageExtractResult(
                text=text_layer.strip(),
                confidence=0.95,
                provider="digital_text",
                source_type="DIGITAL",
                model="mock",
                prompt_id="page_ocr",
                prompt_version="2",
            )
        base = (
            (text_layer or "").strip()
            or (
                f"[mock OCR page {page_number}] "
                "Gata No. 183/2 Village Example Seller Ram Kumar Buyer Sita Devi "
                "Sale Deed dated 12/03/2020 Registration No. REG-1234 "
                "Area 0.5 acre"
            )
        )
        text = diverge_survey_in_text(base) if pass_id == "ocr_b" else base
        return PageExtractResult(
            text=text,
            confidence=0.75 if pass_id == "ocr_b" else 0.8,
            provider="mock_vision_b" if pass_id == "ocr_b" else "mock_vision",
            source_type="OCR",
            bbox=[0, 0, 100, 100],
            model="mock",
            prompt_id="page_ocr_b" if pass_id == "ocr_b" else "page_ocr",
            prompt_version="2",
        )

    async def extract_structured(
        self,
        *,
        doc_type: str,
        filename: str | None,
        evidence_pages: list[dict[str, Any]],
        focus: str | None = None,
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

        seller = "Ram Kumar" if "ram kumar" in combined else None
        if seller is None:
            if "lakshmi" in combined:
                seller = "Lakshmi Devi"
            elif "unknown seller" in combined:
                seller = "Unknown Seller"
            else:
                # Pull a "Seller X" pattern if present
                import re as _re

                m = _re.search(r"seller\s+([A-Za-z][A-Za-z .]+?)(?:\s+buyer|\s+sale|\s+dated|$)", combined, _re.I)
                seller = m.group(1).strip().title() if m else "Unknown Seller"
        buyer = "Sita Devi" if "sita devi" in combined else None
        if buyer is None:
            if "ramesh" in combined:
                buyer = "Ramesh Kumar"
            else:
                import re as _re

                m = _re.search(r"buyer\s+([A-Za-z][A-Za-z .]+?)(?:\s+sale|\s+dated|\s+area|$)", combined, _re.I)
                buyer = m.group(1).strip().title() if m else "Unknown Buyer"
        survey = "183/2" if "183/2" in combined else None
        if survey is None and "184/1" in combined:
            survey = "184/1"
        date_raw = None
        if "12/03/2020" in combined:
            date_raw = "12/03/2020"
        elif "15/06/2005" in combined:
            date_raw = "15/06/2005"
        elif "dated" in combined:
            date_raw = "12/03/2020"
        reg = "REG-1234" if "reg-1234" in combined else None
        if reg is None and "reg-2005" in combined:
            reg = "REG-2005"
        area = None
        if "0.5 acre" in combined or ("acre" in combined and "0.5" in combined):
            area = "0.5 acre"
        elif "1.0 acre" in combined or "1 acre" in combined:
            area = "1.0 acre"

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

        referenced: list[ReferencedDocumentPayload] = []
        if "prior sale deed" in combined or "previous sale deed" in combined or "sale deed 2005" in combined:
            year = "2005" if "2005" in combined else None
            label = f"Sale Deed {year}" if year else "Prior sale deed"
            referenced.append(
                ReferencedDocumentPayload(
                    label=label,
                    doc_type="sale_deed",
                    year=year,
                    page_number=page_num,
                    evidence_snippet=snippet,
                    confidence=0.75,
                )
            )
            facts.append(
                CandidateFactPayload(
                    fact_type="document.reference",
                    predicate="references_instrument",
                    value_text=label,
                    page_number=page_num,
                    evidence_snippet=snippet,
                    confidence=0.75,
                    attributes={"doc_type": "sale_deed", "year": year},
                )
            )
        if "mutation" in combined and "mutation" not in (filename or "").lower():
            referenced.append(
                ReferencedDocumentPayload(
                    label="Mutation",
                    doc_type="mutation",
                    page_number=page_num,
                    evidence_snippet=snippet,
                )
            )

        return StructuredExtractResult(
            persons=persons,
            parcels=parcels,
            ownership_events=events,
            encumbrances=encumbrances,
            referenced_documents=referenced,
            facts=facts,
            model="mock",
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Match Gemini text-embedding-004 dimensionality (768) for pgvector.
        out: list[list[float]] = []
        for t in texts:
            vec = [0.0] * 768
            if not t:
                out.append(vec)
                continue
            for i, ch in enumerate(t.encode("utf-8", errors="ignore")[:2048]):
                idx = (ch * 31 + i * 17) % 768
                vec[idx] += 1.0
            # L2-normalize-ish scale
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            out.append([x / norm for x in vec])
        return out

    async def synthesize_answer(
        self, *, system: str, packed_context: dict[str, Any]
    ) -> dict[str, Any]:
        hits = packed_context.get("hits") or []
        conflicts = packed_context.get("conflicts") or []
        if conflicts:
            return {
                "answer": "Conflicts are present; see packed conflict list. Do not invent a winner.",
                "status": "CONFLICTING",
                "open_questions": [c.get("summary") for c in conflicts[:3]],
                "model": "mock",
                "input_tokens": 10,
                "output_tokens": 20,
            }
        if not hits:
            return {
                "answer": "Insufficient evidence in packed context.",
                "status": "INSUFFICIENT_EVIDENCE",
                "open_questions": [],
                "model": "mock",
                "input_tokens": 5,
                "output_tokens": 10,
            }
        lines = [f"- {h.get('snippet')}" for h in hits[:5]]
        return {
            "answer": "Mock synthesis from packed hits:\n" + "\n".join(lines),
            "status": "PARTIALLY_SUPPORTED",
            "open_questions": [],
            "model": "mock",
            "input_tokens": 10,
            "output_tokens": 20,
        }

    async def reason_legal_findings(
        self, *, findings_payload: list[dict[str, Any]]
    ) -> dict[str, Any]:
        from packages.ai.routing import RouteEngine, route_for_stage

        route = route_for_stage("legal_layer2")
        assert route == RouteEngine.PRO
        notes = []
        for i, f in enumerate(findings_payload):
            notes.append(
                {
                    "index": i,
                    "note": (
                        f"Layer-2 ({route.value}) review of {f.get('category')}: "
                        f"{f.get('statement')} Status remains {f.get('status')}."
                    ),
                    "recommended_action": f.get("recommended_action"),
                }
            )
        return {
            "notes": notes,
            "model": "mock-pro",
            "route": route.value,
            "input_tokens": 20,
            "output_tokens": 40,
        }
