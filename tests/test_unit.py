"""Unit tests for Phase 0/1 helpers."""

from packages.domain.auth import generate_api_key, hash_api_key, verify_api_key
from packages.domain.storage import FilesystemStorage, sha256_bytes
from packages.domain.textutil import normalize_text


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
