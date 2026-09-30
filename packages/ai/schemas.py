"""Structured extraction result schemas (LLM proposes; store decides)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CandidateFactPayload:
    fact_type: str
    predicate: str
    value_text: str | None = None
    value_normalized: str | None = None
    unit: str | None = None
    page_number: int | None = None
    evidence_snippet: str | None = None
    confidence: float = 0.5
    verification_state: str = "EXTRACTED"
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class PersonPayload:
    name: str
    role: str | None = None
    aliases: list[str] = field(default_factory=list)
    identifiers: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ParcelPayload:
    label: str | None = None
    identifiers: list[dict[str, str]] = field(default_factory=list)
    area_raw: str | None = None
    village: str | None = None


@dataclass
class OwnershipEventPayload:
    event_type: str = "sale"
    event_date_raw: str | None = None
    registration_number: str | None = None
    consideration_raw: str | None = None
    seller_names: list[str] = field(default_factory=list)
    buyer_names: list[str] = field(default_factory=list)
    share_text: str | None = None


@dataclass
class EncumbrancePayload:
    encumbrance_type: str
    status: str | None = None
    holder: str | None = None
    amount_raw: str | None = None
    page_number: int | None = None
    evidence_snippet: str | None = None
    confidence: float = 0.5


@dataclass
class ReferencedDocumentPayload:
    label: str
    doc_type: str | None = None
    year: str | None = None
    page_number: int | None = None
    evidence_snippet: str | None = None
    confidence: float = 0.6


@dataclass
class StructuredExtractResult:
    persons: list[PersonPayload] = field(default_factory=list)
    parcels: list[ParcelPayload] = field(default_factory=list)
    ownership_events: list[OwnershipEventPayload] = field(default_factory=list)
    encumbrances: list[EncumbrancePayload] = field(default_factory=list)
    referenced_documents: list[ReferencedDocumentPayload] = field(default_factory=list)
    facts: list[CandidateFactPayload] = field(default_factory=list)
    model: str = "mock"
    prompt_id: str = "structured_extract"
    schema_version: str = "extraction.v1"
    prompt_version: str = "1"
    temperature: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
