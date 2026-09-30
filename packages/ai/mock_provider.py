"""Deterministic mock LLM for local/CI without Gemini."""

from __future__ import annotations

from packages.ai.provider import ClassificationResult, PageExtractResult


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
            "Sale Deed dated 12/03/2020 Registration No. REG-1234"
        )
        return PageExtractResult(
            text=mock_text,
            confidence=0.8,
            provider="mock_vision",
            source_type="OCR",
            bbox=[0, 0, 100, 100],
            model="mock",
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t) % 97) / 97.0] * 8 for t in texts]
