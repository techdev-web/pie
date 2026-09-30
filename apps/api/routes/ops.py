"""Phase 8 ops: summary metrics, cost budgets, eval triggers."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import AuthContext, require_auth
from apps.api.schemas import CostBudgetOut, EvalRunOut, EvalRunRequest, OpsSummaryOut
from packages.domain.db import get_session
from packages.eval.runner import run_eval_pack
from packages.ops import build_ops_summary, refresh_budget, sum_case_costs

router = APIRouter(tags=["ops"])


@router.get("/ops/summary", response_model=OpsSummaryOut)
async def ops_summary(
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> OpsSummaryOut:
    data = await build_ops_summary(session, auth.tenant_id)
    await session.commit()
    return OpsSummaryOut(**data)


@router.get("/ops/costs", response_model=CostBudgetOut)
async def ops_costs(
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> CostBudgetOut:
    budget = await refresh_budget(session, auth.tenant_id)
    cases = await sum_case_costs(session, auth.tenant_id)
    await session.commit()
    return CostBudgetOut(
        period_start=budget.period_start.isoformat(),
        period_end=budget.period_end.isoformat(),
        budget_usd=float(budget.budget_usd),
        spent_usd=float(budget.spent_usd),
        alert_threshold_pct=budget.alert_threshold_pct,
        status=budget.status,
        cost_per_case=cases,
    )


@router.post("/ops/evals/run", response_model=EvalRunOut)
async def run_eval(
    body: EvalRunRequest,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> EvalRunOut:
    result = await run_eval_pack(
        session,
        pack_name=body.pack_name,
        persist=body.persist,
        tenant_id=auth.tenant_id,
    )
    await session.commit()
    return EvalRunOut(**result)
