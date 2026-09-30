"""API key hashing helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_api_key(prefix: str = "pie") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def verify_api_key(raw_key: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(raw_key), key_hash)


def key_prefix(raw_key: str, n: int = 12) -> str:
    return raw_key[:n]
