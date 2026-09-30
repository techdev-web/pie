from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str | None = None


class CaseOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentInitiate(BaseModel):
    filename: str
    role: str = "supporting"
    content_type: str = "application/pdf"


class DocumentInitiateOut(BaseModel):
    document_id: uuid.UUID
    upload_mode: str
    upload_url: str
    deduped: bool = False
    message: str | None = None


class DocumentOut(BaseModel):
    id: uuid.UUID
    content_hash: str
    mime_type: str
    size_bytes: int
    source_filename: str | None
    page_count: int | None
    upload_status: str
    processing_version: str
    storage_uri: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentCompleteOut(BaseModel):
    document: DocumentOut
    job_id: uuid.UUID
    deduped: bool


class JobOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    document_id: uuid.UUID
    status: str
    processing_version: str
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    stages: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class EvidenceOut(BaseModel):
    evidence_id: str
    document_id: uuid.UUID
    case_id: uuid.UUID
    page_number: int
    text: str
    normalized_text: str
    bbox: list[Any] | None
    source_type: str
    ocr_provider: str
    ocr_confidence: float | None
    extraction_version: str

    model_config = {"from_attributes": True}


class PageOut(BaseModel):
    id: uuid.UUID
    page_number: int
    width: int | None
    height: int | None
    image_url: str | None
    has_text_layer: bool
    quality_label: str | None
    ocr_route: str | None


class CaseDetailOut(CaseOut):
    documents: list[DocumentOut] = Field(default_factory=list)


class FactOut(BaseModel):
    id: uuid.UUID
    fact_id: str
    case_id: uuid.UUID
    document_id: uuid.UUID | None
    fact_type: str
    subject_type: str | None
    subject_id: uuid.UUID | None
    predicate: str
    value_text: str | None
    value_normalized: str | None
    value_json: dict[str, Any] | None
    unit: str | None
    verification_state: str
    confidence: float | None
    extraction_version: str
    created_at: datetime
    evidence_count: int = 0

    model_config = {"from_attributes": True}


class FactDetailOut(FactOut):
    evidence: list[EvidenceOut] = Field(default_factory=list)
