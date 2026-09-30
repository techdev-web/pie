"""Ops summary metrics: latency, OCR confidence, review backlog, verified ratio."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models import (
    EvidenceItem,
    Fact,
    ProcessingStageRun,
    ReviewTask,
)
from packages.ops.cost import refresh_budget, sum_case_costs


def _percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


async def stage_latency_stats(
    session: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(
                ProcessingStageRun.stage_name,
                ProcessingStageRun.started_at,
                ProcessingStageRun.finished_at,
            ).where(
                ProcessingStageRun.tenant_id == tenant_id,
                ProcessingStageRun.started_at.is_not(None),
                ProcessingStageRun.finished_at.is_not(None),
                ProcessingStageRun.status == "succeeded",
            )
        )
    ).all()
    by_stage: dict[str, list[float]] = {}
    for stage_name, started, finished in rows:
        ms = (finished - started).total_seconds() * 1000.0
        by_stage.setdefault(stage_name, []).append(ms)
    out: dict[str, Any] = {}
    for stage_name, vals in by_stage.items():
        vals.sort()
        out[stage_name] = {
            "count": len(vals),
            "p50_ms": _percentile(vals, 0.50),
            "p95_ms": _percentile(vals, 0.95),
            "max_ms": vals[-1] if vals else None,
        }
    return out


async def ocr_confidence_distribution(
    session: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, Any]:
    confs = (
        await session.execute(
            select(EvidenceItem.ocr_confidence).where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.ocr_confidence.is_not(None),
            )
        )
    ).scalars().all()
    buckets = {"lt_0.5": 0, "0.5_0.7": 0, "0.7_0.9": 0, "gte_0.9": 0}
    for c in confs:
        v = float(c)
        if v < 0.5:
            buckets["lt_0.5"] += 1
        elif v < 0.7:
            buckets["0.5_0.7"] += 1
        elif v < 0.9:
            buckets["0.7_0.9"] += 1
        else:
            buckets["gte_0.9"] += 1
    return {"count": len(confs), "buckets": buckets}


async def review_backlog(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    open_count = (
        await session.execute(
            select(func.count())
            .select_from(ReviewTask)
            .where(
                ReviewTask.tenant_id == tenant_id,
                ReviewTask.status == "OPEN",
            )
        )
    ).scalar_one()
    by_severity = (
        await session.execute(
            select(ReviewTask.severity, func.count())
            .where(
                ReviewTask.tenant_id == tenant_id,
                ReviewTask.status == "OPEN",
            )
            .group_by(ReviewTask.severity)
        )
    ).all()
    return {
        "open_count": int(open_count or 0),
        "by_severity": {str(s or "UNKNOWN"): int(n) for s, n in by_severity},
    }


async def verified_fact_ratio(
    session: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, Any]:
    total = (
        await session.execute(
            select(func.count()).select_from(Fact).where(Fact.tenant_id == tenant_id)
        )
    ).scalar_one()
    verified = (
        await session.execute(
            select(func.count())
            .select_from(Fact)
            .where(
                Fact.tenant_id == tenant_id,
                Fact.verification_state.in_(("VERIFIED", "SUPPORTED", "CORROBORATED")),
            )
        )
    ).scalar_one()
    total_i = int(total or 0)
    verified_i = int(verified or 0)
    return {
        "total_facts": total_i,
        "verified_facts": verified_i,
        "ratio": (verified_i / total_i) if total_i else None,
    }


async def build_ops_summary(
    session: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, Any]:
    budget = await refresh_budget(session, tenant_id)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cost_per_case": await sum_case_costs(session, tenant_id),
        "stage_latency": await stage_latency_stats(session, tenant_id),
        "ocr_confidence": await ocr_confidence_distribution(session, tenant_id),
        "review_backlog": await review_backlog(session, tenant_id),
        "verified_fact_ratio": await verified_fact_ratio(session, tenant_id),
        "budget": {
            "period_start": budget.period_start.isoformat(),
            "period_end": budget.period_end.isoformat(),
            "budget_usd": float(budget.budget_usd),
            "spent_usd": float(budget.spent_usd),
            "alert_threshold_pct": budget.alert_threshold_pct,
            "status": budget.status,
        },
    }
