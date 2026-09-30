"""Cost budgets, spend aggregation, and alert status."""

from __future__ import annotations

import uuid
from calendar import monthrange
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.config import get_settings
from packages.domain.models import CostBudget, ModelRun
from packages.observability import get_logger

log = get_logger("ops.cost")


def _month_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    last_day = monthrange(now.year, now.month)[1]
    end = datetime(now.year, now.month, last_day, 23, 59, 59, tzinfo=timezone.utc)
    return start, end


async def sum_tenant_spend(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    period_start: datetime,
    period_end: datetime,
) -> Decimal:
    q = await session.execute(
        select(func.coalesce(func.sum(ModelRun.cost_usd), 0)).where(
            ModelRun.tenant_id == tenant_id,
            ModelRun.created_at >= period_start,
            ModelRun.created_at <= period_end,
        )
    )
    raw = q.scalar_one()
    return Decimal(str(raw or 0))


async def sum_case_costs(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    case_ids: list[uuid.UUID] | None = None,
) -> list[dict[str, Any]]:
    q = (
        select(
            ModelRun.case_id,
            func.coalesce(func.sum(ModelRun.cost_usd), 0).label("cost_usd"),
            func.count(ModelRun.id).label("run_count"),
            func.coalesce(func.sum(ModelRun.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(ModelRun.output_tokens), 0).label("output_tokens"),
        )
        .where(ModelRun.tenant_id == tenant_id, ModelRun.case_id.is_not(None))
        .group_by(ModelRun.case_id)
    )
    if case_ids:
        q = q.where(ModelRun.case_id.in_(case_ids))
    rows = (await session.execute(q)).all()
    return [
        {
            "case_id": str(r.case_id),
            "cost_usd": float(r.cost_usd or 0),
            "run_count": int(r.run_count),
            "input_tokens": int(r.input_tokens or 0),
            "output_tokens": int(r.output_tokens or 0),
        }
        for r in rows
    ]


def _status_for_spend(spent: Decimal, budget: Decimal, alert_pct: int) -> str:
    if budget <= 0:
        return "OK"
    ratio = float(spent / budget) * 100
    if ratio >= 100:
        return "EXCEEDED"
    if ratio >= alert_pct:
        return "WARNING"
    return "OK"


async def get_or_create_budget(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> CostBudget:
    settings = get_settings()
    period_start, period_end = _month_bounds(now)
    existing = (
        await session.execute(
            select(CostBudget).where(
                CostBudget.tenant_id == tenant_id,
                CostBudget.period_start == period_start,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    budget = CostBudget(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        period_start=period_start,
        period_end=period_end,
        budget_usd=Decimal(str(settings.cost_budget_default_usd)),
        alert_threshold_pct=settings.cost_budget_alert_threshold_pct,
        spent_usd=Decimal("0"),
        status="OK",
    )
    session.add(budget)
    await session.flush()
    return budget


async def refresh_budget(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> CostBudget:
    budget = await get_or_create_budget(session, tenant_id, now=now)
    spent = await sum_tenant_spend(
        session,
        tenant_id,
        period_start=budget.period_start,
        period_end=budget.period_end,
    )
    prev_status = budget.status
    budget.spent_usd = spent
    budget.status = _status_for_spend(spent, budget.budget_usd, budget.alert_threshold_pct)
    await session.flush()
    if budget.status != prev_status and budget.status in ("WARNING", "EXCEEDED"):
        log.warning(
            "cost_budget_alert",
            tenant_id=str(tenant_id),
            status=budget.status,
            spent_usd=float(spent),
            budget_usd=float(budget.budget_usd),
            alert_threshold_pct=budget.alert_threshold_pct,
        )
    return budget
