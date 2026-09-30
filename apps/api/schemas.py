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


class ConflictFactRef(BaseModel):
    fact_id: str
    document_id: uuid.UUID | None = None
    fact_type: str
    predicate: str
    value_text: str | None = None
    value_normalized: str | None = None
    verification_state: str


class ConflictOut(BaseModel):
    id: uuid.UUID
    conflict_id: str
    case_id: uuid.UUID
    conflict_type: str
    severity: str
    status: str
    summary: str
    details: dict[str, Any] | None = None
    reconciliation_version: str
    created_at: datetime
    facts: list[ConflictFactRef] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class MissingEvidenceOut(BaseModel):
    id: uuid.UUID
    gap_id: str
    case_id: uuid.UUID
    gap_type: str
    referenced_from_document_id: uuid.UUID | None
    referenced_label: str
    required_doc_type: str | None
    status: str
    summary: str
    details: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class GraphNodeOut(BaseModel):
    node_id: str
    graph_type: str
    node_type: str
    label: str
    status: str | None = None
    ref_table: str | None = None
    ref_id: uuid.UUID | None = None
    properties: dict[str, Any] | None = None


class GraphEdgeOut(BaseModel):
    edge_id: str
    graph_type: str
    from_node_id: str
    to_node_id: str
    edge_type: str
    status: str | None = None
    properties: dict[str, Any] | None = None


class GraphOut(BaseModel):
    graph_type: str
    nodes: list[GraphNodeOut] = Field(default_factory=list)
    edges: list[GraphEdgeOut] = Field(default_factory=list)


class CompletenessScorecardOut(BaseModel):
    dimensions: dict[str, str]
    open_conflicts_count: int
    missing_evidence_count: int
    case_fingerprint: str
    reconciliation_version: str
    created_at: datetime | None = None


# --- Phase 6 response shapes (used by intelligence) ---


class RiskDriverOut(BaseModel):
    code: str
    label: str
    weight: int
    source_kind: str
    source_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RiskSnapshotOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    case_fingerprint: str
    risk_level: str
    score: int
    weights_version: str
    drivers: list[RiskDriverOut] = Field(default_factory=list)
    ownership_timeline: list[dict[str, Any]] = Field(default_factory=list)
    confidence_profiles: list[dict[str, Any]] = Field(default_factory=list)
    disclaimer: str
    details: dict[str, Any] | None = None
    engine_version: str
    created_at: datetime

    model_config = {"from_attributes": True}


class LegalFindingOut(BaseModel):
    id: uuid.UUID
    finding_id: str
    case_id: uuid.UUID
    category: str
    severity: str
    statement: str
    status: str
    layer: str
    evidence_ids: list[Any] = Field(default_factory=list)
    related_fact_ids: list[Any] = Field(default_factory=list)
    related_conflict_id: str | None = None
    related_gap_id: str | None = None
    missing_evidence: list[Any] = Field(default_factory=list)
    recommended_action: str | None = None
    details: dict[str, Any] | None = None
    engine_version: str
    created_at: datetime

    model_config = {"from_attributes": True}


class GeoFindingOut(BaseModel):
    id: uuid.UUID
    finding_id: str
    case_id: uuid.UUID
    parcel_id: uuid.UUID | None = None
    finding_type: str
    severity: str
    status: str
    statement: str
    geometry_valid: bool | None = None
    identity_match: str | None = None
    boundary_consistent: str | None = None
    related_fact_ids: list[Any] = Field(default_factory=list)
    details: dict[str, Any] | None = None
    engine_version: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CaseIntelligenceOut(BaseModel):
    case_id: uuid.UUID
    open_conflicts_count: int
    missing_evidence_count: int
    scorecard: CompletenessScorecardOut | None = None
    conflicts: list[ConflictOut] = Field(default_factory=list)
    missing_evidence: list[MissingEvidenceOut] = Field(default_factory=list)
    document_graph: GraphOut | None = None
    entity_event_graph: GraphOut | None = None
    risk: RiskSnapshotOut | None = None
    legal_findings: list[LegalFindingOut] = Field(default_factory=list)
    geo_findings: list[GeoFindingOut] = Field(default_factory=list)
    ownership_timeline: list[dict[str, Any]] = Field(default_factory=list)
    confidence_profiles: list[dict[str, Any]] = Field(default_factory=list)


# --- Phase 4 ---


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: uuid.UUID | None = None


class EvidenceCitationOut(BaseModel):
    document_id: str | None = None
    page: int | None = None
    snippet: str = ""
    bbox: list[Any] = Field(default_factory=list)
    evidence_id: str | None = None
    fact_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    status: str
    evidence: list[EvidenceCitationOut] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    missing_evidence: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    retrieval_trace_id: uuid.UUID
    query_class: str | None = None
    extracted_ids: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_paths: list[str] = Field(default_factory=list)
    guardrail_warnings: list[str] = Field(default_factory=list)


class ConversationOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationMessageOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    answer_status: str | None = None
    retrieval_trace_id: uuid.UUID | None = None
    answer_payload: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CaseMemoryOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    version: int
    key_facts_summary: list[Any] = Field(default_factory=list)
    open_questions: list[Any] = Field(default_factory=list)
    user_preferences: dict[str, Any] = Field(default_factory=dict)
    last_reconciliation_snapshot_id: uuid.UUID | None = None
    summary_text: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FactConfirmRequest(BaseModel):
    note: str | None = None


class FactRejectRequest(BaseModel):
    note: str | None = None
    alternate_value: str | None = None


class MemoryWritebackOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    signal_type: str
    fact_id: uuid.UUID | None
    prior_state: str | None
    new_state: str | None
    note: str | None
    review_decision_id: uuid.UUID | None = None
    details: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Phase 5 ---


class ReviewTaskOut(BaseModel):
    id: uuid.UUID
    task_id: str
    case_id: uuid.UUID
    task_type: str
    severity: str
    status: str
    title: str
    summary: str
    source_kind: str
    source_ref_id: str | None = None
    fingerprint: str
    related_fact_ids: list[Any] = Field(default_factory=list)
    related_conflict_id: str | None = None
    related_gap_id: str | None = None
    details: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None

    model_config = {"from_attributes": True}


class ReviewDecisionOut(BaseModel):
    id: uuid.UUID
    decision_id: str
    review_task_id: uuid.UUID
    case_id: uuid.UUID
    action: str
    actor: str
    reason: str | None = None
    note: str | None = None
    selected_fact_id: str | None = None
    prior_states: dict[str, Any] | None = None
    effects: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewDecisionRequest(BaseModel):
    action: str = Field(
        description="approve | reject | merge | split | request_docs | annotate"
    )
    reason: str | None = None
    note: str | None = None
    selected_fact_id: str | None = None
    preferred_name: str | None = None
    requested_doc_label: str | None = None


class ReviewFlagRequest(BaseModel):
    summary: str = Field(min_length=1, max_length=4000)
    conversation_message_id: str | None = None
    fact_id: str | None = None
    severity: str = "MEDIUM"


class AuditEventOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID | None
    actor: str
    action: str
    resource_type: str
    resource_id: str | None = None
    details: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewDecisionResponse(BaseModel):
    task: ReviewTaskOut
    decision: ReviewDecisionOut

