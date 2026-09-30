"""Unit tests for Phase 0/1 helpers."""

import pytest

from packages.ai.mock_provider import MockProvider
from packages.domain.auth import generate_api_key, hash_api_key, verify_api_key
from packages.domain.storage import FilesystemStorage, sha256_bytes
from packages.domain.textutil import normalize_text
from packages.pipeline.ocr_dual import (
    diverge_survey_in_text,
    is_critical_page_text,
    ocr_texts_diverge,
)
from packages.retrieval.embeddings import EMBEDDING_DIMS, cosine_similarity, pad_or_trim_vector


def test_sha256_stable():
    assert sha256_bytes(b"hello") == sha256_bytes(b"hello")
    assert sha256_bytes(b"hello") != sha256_bytes(b"world")


def test_api_key_roundtrip():
    key = generate_api_key()
    h = hash_api_key(key)
    assert verify_api_key(key, h)
    assert not verify_api_key(key + "x", h)


def test_normalize_text():
    assert normalize_text("  Gata  No.\t183/2  ") == "gata no. 183/2"


def test_filesystem_storage_no_overwrite(tmp_path):
    store = FilesystemStorage(str(tmp_path))
    uri1 = store.put_bytes("a/b.pdf", b"one", "application/pdf")
    uri2 = store.put_bytes("a/b.pdf", b"two", "application/pdf")
    assert uri1 == uri2
    assert store.get_bytes("a/b.pdf") == b"one"


def test_critical_page_and_conflicting_ocr_helpers():
    text_a = "Gata No. 183/2 Seller Ram Kumar Registration No. REG-1234"
    assert is_critical_page_text(text_a)
    assert not is_critical_page_text("Hello blank cover page")
    text_b = diverge_survey_in_text(text_a)
    assert "183/7" in text_b or text_b != text_a
    assert ocr_texts_diverge(text_a, text_b)
    assert not ocr_texts_diverge(text_a, text_a)


@pytest.mark.asyncio
async def test_mock_ocr_b_diverges_on_survey():
    provider = MockProvider()
    a = await provider.extract_page(
        page_number=1,
        text_layer="Gata No. 183/2 Village Example",
        image_bytes=b"x",
        force_vision=True,
        pass_id="ocr_a",
    )
    b = await provider.extract_page(
        page_number=1,
        text_layer="Gata No. 183/2 Village Example",
        image_bytes=b"x",
        force_vision=True,
        pass_id="ocr_b",
    )
    assert ocr_texts_diverge(a.text, b.text)


@pytest.mark.asyncio
async def test_mock_embed_is_768d():
    provider = MockProvider()
    vectors = await provider.embed(["Gata 183/2", "owner Ram"])
    assert len(vectors) == 2
    assert all(len(v) == EMBEDDING_DIMS for v in vectors)
    assert pad_or_trim_vector([1.0, 2.0], 4) == [1.0, 2.0, 0.0, 0.0]
    assert cosine_similarity(vectors[0], vectors[0]) == pytest.approx(1.0)
