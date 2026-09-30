"""Thin Gemini/mock agents for Phase 2 semantic extraction."""

from __future__ import annotations

from typing import Any

from packages.ai.provider import LLMProvider, get_llm_provider
from packages.ai.schemas import StructuredExtractResult


class DocumentClassifier:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or get_llm_provider()

    async def run(self, *, filename: str | None, sample_text: str):
        return await self.provider.classify(filename=filename, sample_text=sample_text)


class _FocusedExtractor:
    focus: str = "full"

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or get_llm_provider()

    async def run(self, **kwargs: Any) -> StructuredExtractResult:
        kwargs.setdefault("focus", self.focus)
        return await self.provider.extract_structured(**kwargs)


class EntityExtractor(_FocusedExtractor):
    """Extract persons/orgs from evidence text via structured extract."""

    focus = "entities"


class ParcelExtractor(_FocusedExtractor):
    focus = "parcels"


class TransactionExtractor(_FocusedExtractor):
    focus = "transactions"


class OwnershipEventExtractor(_FocusedExtractor):
    focus = "ownership"


class EncumbranceExtractor(_FocusedExtractor):
    focus = "encumbrances"


class FullStructuredExtractor(_FocusedExtractor):
    """Default pipeline extractor — one comprehensive pass."""

    focus = "full"
