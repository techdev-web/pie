"""PII-aware field redaction for structured logs and audit payloads."""

from __future__ import annotations

import re
from typing import Any

# Common Indian / general PII patterns (conservative; over-redact preferred).
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\-\s]{8,}\d)(?!\d)")
_AADHAAR_RE = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")
_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_PASSPORT_RE = re.compile(r"\b[A-Z][0-9]{7}\b")

_SENSITIVE_KEYS = {
    "aadhaar",
    "aadhar",
    "pan",
    "passport",
    "phone",
    "mobile",
    "email",
    "ssn",
    "dob",
    "date_of_birth",
    "bank_account",
    "account_number",
    "ifsc",
    "password",
    "secret",
    "api_key",
    "token",
    "authorization",
}


def redact_string(value: str) -> str:
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    text = _AADHAAR_RE.sub("[REDACTED_AADHAAR]", text)
    text = _PAN_RE.sub("[REDACTED_PAN]", text)
    text = _PASSPORT_RE.sub("[REDACTED_PASSPORT]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    return text


def redact_value(value: Any, *, key: str | None = None) -> Any:
    if key and key.lower() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, dict):
        return {k: redact_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v, key=key) for v in value]
    return value


def redact_event_dict(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor: redact PII from log event fields."""
    return {k: redact_value(v, key=str(k)) for k, v in event_dict.items()}
