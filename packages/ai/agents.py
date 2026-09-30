"""Thin Gemini/mock agents for Phase 2 semantic extraction."""

from __future__ import annotations

from packages.ai.provider import LLMProvider, get_llm_provider
from packages.ai.schemas import StructuredExtractResult


class DocumentClassifier:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or get_llm_provider()

    async def run(self, *, filename: str | None, sample_text: str):
        return await self.provider.classify(filename=filename, sample_text=sample_text)


class EntityExtractor:
    """Extract persons/orgs from evidence text via structured extract."""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or get_llm_provider()

    async def run(self, **kwargs) -> StructuredExtractResult:
        result = await self.provider.extract_structured(**kwargs)
        return result


class ParcelExtractor:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or get_llm_provider()

    async def run(self, **kwargs) -> StructuredExtractResult:
        return await self.provider.extract_structured(**kwargs)


class TransactionExtractor:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or get_llm_provider()

    async def run(self, **kwargs) -> StructuredExtractResult:
        return await self.provider.extract_structured(**kwargs)


class OwnershipEventExtractor:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or get_llm_provider()

    async def run(self, **kwargs) -> StructuredExtractResult:
        return await self.provider.extract_structured(**kwargs)


class EncumbranceExtractor:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or get_llm_provider()

    async def run(self, **kwargs) -> StructuredExtractResult:
        return await self.provider.extract_structured(**kwargs)
