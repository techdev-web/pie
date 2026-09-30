"""Phase 3 case intelligence: conflicts, gaps, graphs, scorecard."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import AuthContext, require_auth
from apps.api.queue import enqueue_reconcile_case
from apps.api.schemas import (
    CaseIntelligenceOut,
    CompletenessScorecardOut,
    ConflictFactRef,
    ConflictOut,
    GraphEdgeOut,
    GraphNodeOut,
    GraphOut,
    MissingEvidenceOut,
)
from packages.domain.db import get_session
from packages.domain.models import (
    Case,
    CaseCompletenessSnapshot,
    Conflict,
    ConflictFact,
    GraphEdge,
    GraphNode,
    MissingEvidence,
)
from packages.pipeline.reconciliation import run_case_reconciliation

router = APIRouter(tags=["intelligence"])


async def _assert_case_access(
    session: AsyncSession, case_id: uuid.UUID, tenant_id: uuid.UUID
) -> Case:
    case = (
        await session.execute(
            select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _conflict_to_out(conflict: Conflict) -> ConflictOut:
    facts: list[ConflictFactRef] = []
    for link in conflict.fact_links or []:
        f = link.fact
        if f is None:
            continue
        facts.append(
            ConflictFactRef(
                fact_id=f.fact_id,
                document_id=f.document_id,
                fact_type=f.fact_type,
                predicate=f.predicate,
                value_text=f.value_text,
                value_normalized=f.value_normalized,
                verification_state=f.verification_state,
            )
        )
    return ConflictOut(
        id=conflict.id,
        conflict_id=conflict.conflict_id,
        case_id=conflict.case_id,
        conflict_type=conflict.conflict_type,
        severity=conflict.severity,
        status=conflict.status,
        summary=conflict.summary,
        details=conflict.details,
        reconciliation_version=conflict.reconciliation_version,
        created_at=conflict.created_at,
        facts=facts,
    )


def _graph_out(
    graph_type: str, nodes: list[GraphNode], edges: list[GraphEdge]
) -> GraphOut:
    return GraphOut(
        graph_type=graph_type,
        nodes=[
            GraphNodeOut(
                node_id=n.node_id,
                graph_type=n.graph_type,
                node_type=n.node_type,
                label=n.label,
                status=n.status,
                ref_table=n.ref_table,
                ref_id=n.ref_id,
                properties=n.properties,
            )
            for n in nodes
            if n.graph_type == graph_type
        ],
        edges=[
            GraphEdgeOut(
                edge_id=e.edge_id,
                graph_type=e.graph_type,
                from_node_id=e.from_node_id,
                to_node_id=e.to_node_id,
                edge_type=e.edge_type,
                status=e.status,
                properties=e.properties,
            )
            for e in edges
            if e.graph_type == graph_type
        ],
    )


@router.get("/cases/{case_id}/conflicts", response_model=list[ConflictOut])
async def list_conflicts(
    case_id: uuid.UUID,
    status: str | None = Query(default="OPEN"),
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ConflictOut]:
    await _assert_case_access(session, case_id, auth.tenant_id)
    q = (
        select(Conflict)
        .options(selectinload(Conflict.fact_links).selectinload(ConflictFact.fact))
        .where(Conflict.case_id == case_id, Conflict.tenant_id == auth.tenant_id)
        .order_by(Conflict.created_at.desc())
    )
    if status:
        q = q.where(Conflict.status == status)
    rows = list((await session.execute(q)).scalars().unique().all())
    return [_conflict_to_out(c) for c in rows]


@router.get("/cases/{case_id}/missing-evidence", response_model=list[MissingEvidenceOut])
async def list_missing_evidence(
    case_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[MissingEvidenceOut]:
    await _assert_case_access(session, case_id, auth.tenant_id)
    rows = list(
        (
            await session.execute(
                select(MissingEvidence)
                .where(
                    MissingEvidence.case_id == case_id,
                    MissingEvidence.tenant_id == auth.tenant_id,
                )
                .order_by(MissingEvidence.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [MissingEvidenceOut.model_validate(r) for r in rows]


@router.get("/cases/{case_id}/intelligence", response_model=CaseIntelligenceOut)
async def get_intelligence(
    case_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> CaseIntelligenceOut:
    await _assert_case_access(session, case_id, auth.tenant_id)

    conflicts = list(
        (
            await session.execute(
                select(Conflict)
                .options(selectinload(Conflict.fact_links).selectinload(ConflictFact.fact))
                .where(
                    Conflict.case_id == case_id,
                    Conflict.tenant_id == auth.tenant_id,
                    Conflict.status == "OPEN",
                )
                .order_by(Conflict.created_at.desc())
            )
        )
        .scalars()
        .unique()
        .all()
    )
    gaps = list(
        (
            await session.execute(
                select(MissingEvidence).where(
                    MissingEvidence.case_id == case_id,
                    MissingEvidence.tenant_id == auth.tenant_id,
                    MissingEvidence.status.in_(("NOT_PROVIDED", "OPEN")),
                )
            )
        )
        .scalars()
        .all()
    )
    snapshot = (
        await session.execute(
            select(CaseCompletenessSnapshot)
            .where(CaseCompletenessSnapshot.case_id == case_id)
            .order_by(CaseCompletenessSnapshot.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    nodes = list(
        (
            await session.execute(
                select(GraphNode).where(
                    GraphNode.case_id == case_id, GraphNode.tenant_id == auth.tenant_id
                )
            )
        )
        .scalars()
        .all()
    )
    edges = list(
        (
            await session.execute(
                select(GraphEdge).where(
                    GraphEdge.case_id == case_id, GraphEdge.tenant_id == auth.tenant_id
                )
            )
        )
        .scalars()
        .all()
    )

    scorecard = None
    if snapshot:
        scorecard = CompletenessScorecardOut(
            dimensions=snapshot.dimensions,
            open_conflicts_count=snapshot.open_conflicts_count,
            missing_evidence_count=snapshot.missing_evidence_count,
            case_fingerprint=snapshot.case_fingerprint,
            reconciliation_version=snapshot.reconciliation_version,
            created_at=snapshot.created_at,
        )

    return CaseIntelligenceOut(
        case_id=case_id,
        open_conflicts_count=len(conflicts),
        missing_evidence_count=len(gaps),
        scorecard=scorecard,
        conflicts=[_conflict_to_out(c) for c in conflicts],
        missing_evidence=[MissingEvidenceOut.model_validate(g) for g in gaps],
        document_graph=_graph_out("document", nodes, edges),
        entity_event_graph=_graph_out("entity_event", nodes, edges),
    )


@router.post("/cases/{case_id}/reconcile")
async def trigger_reconcile(
    case_id: uuid.UUID,
    sync: bool = Query(default=False, description="Run inline instead of enqueue"),
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _assert_case_access(session, case_id, auth.tenant_id)
    if sync:
        result = await run_case_reconciliation(session, case_id)
        return {"mode": "sync", "result": result}
    await enqueue_reconcile_case(case_id)
    return {"mode": "queued", "case_id": str(case_id)}
