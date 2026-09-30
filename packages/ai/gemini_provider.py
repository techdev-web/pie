"""Gemini-backed LLM provider."""

from __future__ import annotations

import json
import re
from typing import Any

from packages.ai.provider import ClassificationResult, PageExtractResult
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


class GeminiProvider:
    def __init__(self, settings: Settings) -> None:
        from google import genai

        self.settings = settings
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.classify_model = settings.gemini_classify_model
        self.ocr_model = settings.gemini_ocr_model

    def _parse_json(self, raw: str) -> dict[str, Any]:
        raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            raise

    async def classify(self, *, filename: str | None, sample_text: str) -> ClassificationResult:
        prompt = CLASSIFY_PROMPT.format(
            filename=filename or "",
            sample_text=(sample_text or "")[:4000],
        )
        response = self.client.models.generate_content(
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
    ) -> PageExtractResult:
        if text_layer and text_layer.strip() and not force_vision:
            return PageExtractResult(
                text=text_layer.strip(),
                confidence=0.95,
                provider="digital_text",
                source_type="DIGITAL",
                model="digital",
            )

        if not image_bytes:
            return PageExtractResult(
                text=text_layer or "",
                confidence=0.3,
                provider="gemini",
                source_type="OCR",
                model=self.ocr_model,
            )

        from google.genai import types

        parts = [
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            types.Part.from_text(text=OCR_PROMPT + f"\nPage number: {page_number}"),
        ]
        response = self.client.models.generate_content(
            model=self.ocr_model,
            contents=parts,
            config={"temperature": 0.0},
        )
        data = self._parse_json(response.text or '{"text":"","confidence":0.5}')
        usage = getattr(response, "usage_metadata", None)
        return PageExtractResult(
            text=str(data.get("text", "")),
            confidence=float(data.get("confidence", 0.7)),
            provider="gemini",
            source_type="OCR",
            model=self.ocr_model,
            prompt_id="page_ocr",
            prompt_version="1",
            schema_version="ocr.v1",
            temperature=0.0,
            input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            output_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Placeholder for Phase 4 RAG; return deterministic stub vectors if needed.
        result = self.client.models.embed_content(
            model="text-embedding-004",
            contents=texts,
        )
        embeddings = getattr(result, "embeddings", None) or []
        out: list[list[float]] = []
        for emb in embeddings:
            values = getattr(emb, "values", None) or []
            out.append(list(values))
        return out
