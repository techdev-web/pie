"""Gemini-backed LLM provider."""

from __future__ import annotations

import json
import re
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
from packages.config import Settings


CLASSIFY_PROMPT = """You are a document classifier for Indian land/title due diligence.
Return ONLY valid JSON with keys: doc_type (string), confidence (0-1 float), details (object).
Allowed doc_type values: sale_deed, gift_deed, khatauni, mutation, encumbrance_certificate,
release_deed, partition_deed, power_of_attorney, other, unknown.

Filename: {filename}
Sample text:
{sample_text}
"""

OCR_PROMPT = """Extract all readable text from this document page image for land/title due diligence.
Return ONLY valid JSON with keys: text (string), confidence (0-1 float).
Preserve identifiers (survey/gata/khata/registration numbers) exactly.
Do not invent text that is not visible.
"""

OCR_B_PROMPT = """Independent second-pass OCR for critical diligence fields.
Return ONLY valid JSON with keys: text (string), confidence (0-1 float).
Re-read survey/gata/khata numbers, owner names, area, consideration, registration, mortgage, and dates carefully.
Do not copy a prior extraction — read the image independently.
Do not invent text that is not visible.
"""

FOCUS_PREAMBLES = {
    "entities": "Focus on persons/parties (sellers, buyers, owners, donors) and their identifiers.",
    "parcels": "Focus on parcel identifiers (gata/survey/khata), village, and area.",
    "transactions": "Focus on transaction dates, registration numbers, consideration, and deed type.",
    "ownership": "Focus on ownership events, transfers, shares, and chronology cues.",
    "encumbrances": "Focus on mortgages, charges, releases, and encumbrance status.",
    "full": "Extract all candidate facts comprehensively.",
}

LEGAL_LAYER2_PROMPT = """You are a title diligence legal reasoning assistant (Layer 2).
Given Layer-1 legal findings (JSON), add short narrative notes explaining why each finding matters.
Return ONLY valid JSON:
{"notes": [{"index": int, "note": str, "recommended_action": str|null}]}

Hard rules:
- Never upgrade an UNRESOLVED encumbrance/mortgage to clear or released.
- Never invent a release deed or clearance that is not in the findings.
- Keep Layer-1 status authoritative; notes must not contradict status.
- Do not invent new findings; only annotate the supplied list.
"""

STRUCTURED_EXTRACT_PROMPT = """You extract candidate facts for Indian land/title due diligence.
Return ONLY valid JSON matching this schema:
{{
  "persons": [{{"name": str, "role": "seller|buyer|owner|donor|donee|other"|null, "aliases": [str], "identifiers": [{{"id_type": str, "id_value": str}}]}}],
  "parcels": [{{"label": str|null, "identifiers": [{{"id_type": "gata|survey|khata|khasra|plot", "id_value": str}}], "area_raw": str|null, "village": str|null}}],
  "ownership_events": [{{"event_type": "sale|gift|partition|mortgage|release|mutation|other", "event_date_raw": str|null, "registration_number": str|null, "consideration_raw": str|null, "seller_names": [str], "buyer_names": [str], "share_text": str|null}}],
  "encumbrances": [{{"encumbrance_type": str, "status": str|null, "holder": str|null, "amount_raw": str|null, "page_number": int|null, "evidence_snippet": str|null, "confidence": float}}],
  "referenced_documents": [{{"label": str, "doc_type": "sale_deed|mutation|encumbrance_certificate|release_deed|other"|null, "year": str|null, "page_number": int|null, "evidence_snippet": str|null, "confidence": float}}],
  "facts": [{{"fact_type": str, "predicate": str, "value_text": str|null, "page_number": int|null, "evidence_snippet": str|null, "confidence": float, "verification_state": "EXTRACTED|AMBIGUOUS|NOT_FOUND|REQUIRES_REVIEW"}}]
}}

Rules:
- Do not invent identifiers, dates, owners, or encumbrance status.
- If a field is not present in the supplied evidence, use verification_state NOT_FOUND and leave value_text null.
- Cite page_number and a short evidence_snippet from the supplied text for every EXTRACTED fact.
- Prefer fact_type values: party.seller, party.buyer, party.owner, parcel.survey_number, parcel.area, transaction.date, transaction.registration_number, encumbrance.mortgage, document.reference.
- When the document references a prior deed, mutation, EC, or release not itself, add referenced_documents and a document.reference fact.

Document type: {doc_type}
Filename: {filename}
Evidence pages:
{evidence_text}
"""


class GeminiProvider:
    def __init__(self, settings: Settings) -> None:
        from google import genai

        self.settings = settings
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.classify_model = settings.gemini_classify_model
        self.ocr_model = settings.gemini_ocr_model
        self.chat_model = settings.gemini_chat_model
        self.pro_model = settings.gemini_pro_model
        self.embed_model = settings.gemini_embed_model

    def _parse_json(self, raw: str) -> dict[str, Any]:
        raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            raise

    async def _generate_content(self, **kwargs: Any) -> Any:
        """Run sync SDK call off the event loop (avoids blocking arq workers)."""
        import asyncio

        return await asyncio.to_thread(self.client.models.generate_content, **kwargs)

    async def _embed_content(self, **kwargs: Any) -> Any:
        import asyncio

        return await asyncio.to_thread(self.client.models.embed_content, **kwargs)

    async def classify(self, *, filename: str | None, sample_text: str) -> ClassificationResult:
        prompt = CLASSIFY_PROMPT.format(
            filename=filename or "",
            sample_text=(sample_text or "")[:4000],
        )
        response = await self._generate_content(
            model=self.classify_model,
            contents=prompt,
            config={"temperature": 0.0},
        )
        data = self._parse_json(response.text or "{}")
        usage = getattr(response, "usage_metadata", None)
        return ClassificationResult(
            doc_type=str(data.get("doc_type", "unknown")),
            confidence=float(data.get("confidence", 0.5)),
            details=data.get("details") or {},
            model=self.classify_model,
            prompt_id="document_classify",
            prompt_version="1",
            schema_version="classification.v1",
            temperature=0.0,
            input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            output_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
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
        if text_layer and text_layer.strip() and not force_vision and pass_id != "ocr_b":
            return PageExtractResult(
                text=text_layer.strip(),
                confidence=0.95,
                provider="digital_text",
                source_type="DIGITAL",
                model="digital",
                prompt_id="page_ocr",
                prompt_version="2",
            )

        if not image_bytes:
            return PageExtractResult(
                text=text_layer or "",
                confidence=0.3,
                provider="gemini",
                source_type="OCR",
                model=self.ocr_model,
                prompt_id="page_ocr_b" if pass_id == "ocr_b" else "page_ocr",
                prompt_version="2",
            )

        from google.genai import types

        ocr_prompt = OCR_B_PROMPT if pass_id == "ocr_b" else OCR_PROMPT
        parts = [
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            types.Part.from_text(text=ocr_prompt + f"\nPage number: {page_number}"),
        ]
        response = await self._generate_content(
            model=self.ocr_model,
            contents=parts,
            config={"temperature": 0.0},
        )
        data = self._parse_json(response.text or '{"text":"","confidence":0.5}')
        usage = getattr(response, "usage_metadata", None)
        return PageExtractResult(
            text=str(data.get("text", "")),
            confidence=float(data.get("confidence", 0.7)),
            provider="gemini_b" if pass_id == "ocr_b" else "gemini",
            source_type="OCR",
            model=self.ocr_model,
            prompt_id="page_ocr_b" if pass_id == "ocr_b" else "page_ocr",
            prompt_version="2",
            schema_version="ocr.v1",
            temperature=0.0,
            input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            output_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Phase 4 RAG embeddings via Gemini (normalize to 768-d for pgvector)
        from packages.retrieval.embeddings import pad_or_trim_vector

        result = await self._embed_content(
            model=self.embed_model,
            contents=texts,
        )
        embeddings = getattr(result, "embeddings", None) or []
        out: list[list[float]] = []
        for emb in embeddings:
            values = getattr(emb, "values", None) or []
            out.append(pad_or_trim_vector(list(values)))
        return out

    async def synthesize_answer(
        self, *, system: str, packed_context: dict[str, Any]
    ) -> dict[str, Any]:
        from packages.ai.routing import RouteEngine, route_for_stage, model_for_route

        prompt = (
            f"{system}\n\nPacked case context (JSON):\n"
            f"{json.dumps(packed_context, default=str)[:20000]}\n"
        )
        route = route_for_stage(
            "chat",
            risk_signals={
                "conflicts": bool(packed_context.get("conflicts")),
                "query_class": packed_context.get("query_class"),
                "legal_ambiguity": bool(packed_context.get("legal_ambiguity")),
                "material_unresolved_risk": bool(
                    packed_context.get("material_unresolved_risk")
                ),
            },
        )
        if route == RouteEngine.HUMAN:
            return {
                "answer": (
                    "Material unresolved risk requires human review before a "
                    "confident answer can be given."
                ),
                "status": "INSUFFICIENT_EVIDENCE",
                "open_questions": ["Escalate to analyst review"],
                "model": "human",
                "input_tokens": None,
                "output_tokens": None,
            }
        model = model_for_route(route, stage="chat", settings=self.settings) or self.chat_model
        if route == RouteEngine.PRO:
            model = self.pro_model
        response = await self._generate_content(
            model=model,
            contents=prompt,
            config={"temperature": 0.0},
        )
        data = self._parse_json(response.text or "{}")
        usage = getattr(response, "usage_metadata", None)
        return {
            "answer": str(data.get("answer") or response.text or ""),
            "status": str(data.get("status") or "PARTIALLY_SUPPORTED"),
            "open_questions": list(data.get("open_questions") or []),
            "model": model,
            "input_tokens": getattr(usage, "prompt_token_count", None) if usage else None,
            "output_tokens": getattr(usage, "candidates_token_count", None) if usage else None,
        }

    def _parse_structured(self, data: dict[str, Any], *, model: str, usage: Any) -> StructuredExtractResult:
        persons = [
            PersonPayload(
                name=str(p.get("name", "")).strip(),
                role=p.get("role"),
                aliases=list(p.get("aliases") or []),
                identifiers=[
                    {"id_type": str(i.get("id_type", "")), "id_value": str(i.get("id_value", ""))}
                    for i in (p.get("identifiers") or [])
                    if i.get("id_value")
                ],
            )
            for p in (data.get("persons") or [])
            if str(p.get("name", "")).strip()
        ]
        parcels = [
            ParcelPayload(
                label=pr.get("label"),
                identifiers=[
                    {"id_type": str(i.get("id_type", "survey")), "id_value": str(i.get("id_value", ""))}
                    for i in (pr.get("identifiers") or [])
                    if i.get("id_value")
                ],
                area_raw=pr.get("area_raw"),
                village=pr.get("village"),
            )
            for pr in (data.get("parcels") or [])
        ]
        events = [
            OwnershipEventPayload(
                event_type=str(e.get("event_type") or "sale"),
                event_date_raw=e.get("event_date_raw"),
                registration_number=e.get("registration_number"),
                consideration_raw=e.get("consideration_raw"),
                seller_names=list(e.get("seller_names") or []),
                buyer_names=list(e.get("buyer_names") or []),
                share_text=e.get("share_text"),
            )
            for e in (data.get("ownership_events") or [])
        ]
        encumbrances = [
            EncumbrancePayload(
                encumbrance_type=str(enc.get("encumbrance_type") or "unknown"),
                status=enc.get("status"),
                holder=enc.get("holder"),
                amount_raw=enc.get("amount_raw"),
                page_number=enc.get("page_number"),
                evidence_snippet=enc.get("evidence_snippet"),
                confidence=float(enc.get("confidence", 0.5)),
            )
            for enc in (data.get("encumbrances") or [])
        ]
        referenced = [
            ReferencedDocumentPayload(
                label=str(r.get("label") or "Referenced instrument"),
                doc_type=r.get("doc_type"),
                year=str(r["year"]) if r.get("year") is not None else None,
                page_number=r.get("page_number"),
                evidence_snippet=r.get("evidence_snippet"),
                confidence=float(r.get("confidence", 0.6)),
            )
            for r in (data.get("referenced_documents") or [])
        ]
        facts = [
            CandidateFactPayload(
                fact_type=str(f.get("fact_type") or "unknown"),
                predicate=str(f.get("predicate") or "value"),
                value_text=f.get("value_text"),
                page_number=f.get("page_number"),
                evidence_snippet=f.get("evidence_snippet"),
                confidence=float(f.get("confidence", 0.5)),
                verification_state=str(f.get("verification_state") or "EXTRACTED"),
                attributes=dict(f.get("attributes") or {}),
            )
            for f in (data.get("facts") or [])
        ]
        # Ensure referenced docs also become facts if LLM only filled referenced_documents
        existing_ref_labels = {
            (f.value_text or "").lower()
            for f in facts
            if f.fact_type == "document.reference"
        }
        for ref in referenced:
            if ref.label.lower() in existing_ref_labels:
                continue
            facts.append(
                CandidateFactPayload(
                    fact_type="document.reference",
                    predicate="references_instrument",
                    value_text=ref.label,
                    page_number=ref.page_number,
                    evidence_snippet=ref.evidence_snippet,
                    confidence=ref.confidence,
                    attributes={"doc_type": ref.doc_type, "year": ref.year},
                )
            )
        return StructuredExtractResult(
            persons=persons,
            parcels=parcels,
            ownership_events=events,
            encumbrances=encumbrances,
            referenced_documents=referenced,
            facts=facts,
            model=model,
            prompt_id="structured_extract",
            prompt_version="1",
            schema_version="extraction.v1",
            temperature=0.0,
            input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            output_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
        )

    async def extract_structured(
        self,
        *,
        doc_type: str,
        filename: str | None,
        evidence_pages: list[dict[str, Any]],
        focus: str | None = None,
    ) -> StructuredExtractResult:
        evidence_text = "\n\n".join(
            f"--- page {p.get('page_number')} ---\n{(p.get('text') or '')[:6000]}"
            for p in evidence_pages
        )[:24000]
        focus_key = (focus or "full").lower()
        preamble = FOCUS_PREAMBLES.get(focus_key, FOCUS_PREAMBLES["full"])
        prompt = (
            f"Focus directive: {preamble}\n\n"
            + STRUCTURED_EXTRACT_PROMPT.format(
                doc_type=doc_type or "unknown",
                filename=filename or "",
                evidence_text=evidence_text or "(no evidence text)",
            )
        )
        response = await self._generate_content(
            model=self.classify_model,
            contents=prompt,
            config={"temperature": 0.0},
        )
        data = self._parse_json(response.text or "{}")
        usage = getattr(response, "usage_metadata", None)
        return self._parse_structured(data, model=self.classify_model, usage=usage)

    async def reason_legal_findings(
        self, *, findings_payload: list[dict[str, Any]]
    ) -> dict[str, Any]:
        from packages.ai.routing import RouteEngine, model_for_route, route_for_stage

        route = route_for_stage("legal_layer2")
        model = model_for_route(route) if route != RouteEngine.HUMAN else self.pro_model
        prompt = (
            f"{LEGAL_LAYER2_PROMPT}\n\nLayer-1 findings:\n"
            f"{json.dumps(findings_payload, default=str)[:20000]}\n"
        )
        response = await self._generate_content(
            model=model or self.pro_model,
            contents=prompt,
            config={"temperature": 0.0},
        )
        data = self._parse_json(response.text or '{"notes":[]}')
        usage = getattr(response, "usage_metadata", None)
        return {
            "notes": data.get("notes") or [],
            "model": model or self.pro_model,
            "route": route.value,
            "input_tokens": getattr(usage, "prompt_token_count", None) if usage else None,
            "output_tokens": getattr(usage, "candidates_token_count", None) if usage else None,
        }
