"""LLM provider protocol and factory."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from packages.ai.schemas import StructuredExtractResult
from packages.config import Settings, get_settings


@dataclass
class ClassificationResult:
    doc_type: str
    confidence: float
    details: dict[str, Any] = field(default_factory=dict)
    model: str = "mock"
    prompt_id: str = "document_classify"
    prompt_version: str = "1"
    schema_version: str = "classification.v1"
    temperature: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class PageExtractResult:
    text: str
    confidence: float
    provider: str
    source_type: str
    bbox: list[float] | None = None
    model: str = "mock"
    prompt_id: str = "page_ocr"
    prompt_version: str = "1"
    schema_version: str = "ocr.v1"
    temperature: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider(Protocol):
    async def classify(self, *, filename: str | None, sample_text: str) -> ClassificationResult: ...

    async def extract_page(
        self,
        *,
        page_number: int,
        text_layer: str | None,
        image_bytes: bytes | None,
        force_vision: bool = False,
        pass_id: str = "ocr_a",
    ) -> PageExtractResult: ...

    async def extract_structured(
        self,
        *,
        doc_type: str,
        filename: str | None,
        evidence_pages: list[dict[str, Any]],
        focus: str | None = None,
    ) -> StructuredExtractResult: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def synthesize_answer(
        self, *, system: str, packed_context: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def reason_legal_findings(
        self, *, findings_payload: list[dict[str, Any]]
    ) -> dict[str, Any]: ...


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    if settings.use_mock_llm:
        from packages.ai.mock_provider import MockProvider

        return MockProvider()
    from packages.ai.gemini_provider import GeminiProvider

    return GeminiProvider(settings)
