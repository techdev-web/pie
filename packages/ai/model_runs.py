"""Helpers for persisting ModelRun rows with cost and latency."""

from __future__ import annotations

import uuid
from decimal import Decimal

from packages.ai.pricing import estimate_cost_usd
from packages.domain.models import ModelRun


def build_model_run(
    *,
    tenant_id: uuid.UUID,
    stage: str,
    prompt_id: str,
    prompt_version: str,
    model: str,
    temperature: float = 0.0,
    schema_version: str | None = None,
    case_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: int | None = None,
    retry_count: int = 0,
) -> ModelRun:
    cost = estimate_cost_usd(model, input_tokens, output_tokens)
    return ModelRun(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        case_id=case_id,
        document_id=document_id,
        stage=stage,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        model=model,
        temperature=temperature,
        schema_version=schema_version,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        latency_ms=latency_ms,
        retry_count=retry_count,
    )


def cost_as_float(cost: Decimal | None) -> float | None:
    if cost is None:
        return None
    return float(cost)
