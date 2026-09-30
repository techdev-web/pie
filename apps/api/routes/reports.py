"""Phase 7: partner analyze + diligence report endpoints."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import AuthContext, require_scopes
from apps.api.queue import enqueue_analyze_case, enqueue_generate_report
from apps.api.schemas import AnalyzeRequest, AnalyzeResponse, ReportArtifactOut, ReportExportOut
from packages.domain.db import get_session
from packages.domain.models import AuditEvent, Case, CaseDocument, Document, ProcessingJob, ReportArtifact
from packages.domain.storage import get_storage
from packages.observability import get_logger, redact_value
from packages.pipeline.reconciliation import run_case_reconciliation
from packages.reports import generate_case_report, get_latest_report, render_report_pdf

log = get_logger("api.reports")

router = APIRouter(tags=["reports"])


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


def _report_out(artifact: ReportArtifact) -> ReportArtifactOut:
    return ReportArtifactOut(
        id=artifact.id,
        report_id=artifact.report_id,
        case_id=artifact.case_id,
        report_type=artifact.report_type,
        status=artifact.status,
        format=artifact.format,
        version=artifact.version,
        truth_fingerprint=artifact.truth_fingerprint,
        case_fingerprint=artifact.case_fingerprint,
        generator_version=artifact.generator_version,
        title=artifact.title,
        body=artifact.body,
        section_fingerprints=artifact.section_fingerprints or {},
        reused_sections=list(artifact.reused_sections or []),
        rebuild_reason=artifact.rebuild_reason,
        pdf_storage_uri=artifact.pdf_storage_uri,
        parent_report_id=artifact.parent_report_id,
        created_at=artifact.created_at,
        updated_at=artifact.updated_at,
    )


@router.post("/cases/{case_id}/analyze", response_model=AnalyzeResponse)
async def analyze_case(
    case_id: uuid.UUID,
    body: AnalyzeRequest | None = None,
    auth: AuthContext = Depends(require_scopes("analyze")),
    session: AsyncSession = Depends(get_session),
) -> AnalyzeResponse:
    """Enqueue full or incremental case analysis for partner integrations."""
    await _assert_case_access(session, case_id, auth.tenant_id)
    req = body or AnalyzeRequest()
    mode = req.mode
    force = mode == "full" or req.force

    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=auth.tenant_id,
            case_id=case_id,
            actor=str(auth.api_key_id),
            action="case.analyze",
            resource_type="case",
            resource_id=str(case_id),
            details=redact_value(
                {
                    "mode": mode,
                    "force": force,
                    "generate_report": req.generate_report,
                    "sync": req.sync,
                }
            ),
        )
    )
    await session.commit()

    if req.sync:
        result = await run_case_reconciliation(session, case_id, force=force)
        report_id = None
        if req.generate_report:
            artifact = await generate_case_report(
                session,
                case_id=case_id,
                tenant_id=auth.tenant_id,
                triggered_by="analyze.sync",
                rebuild_reason=f"analyze:{mode}",
                actor=str(auth.api_key_id),
            )
            report_id = artifact.report_id
        return AnalyzeResponse(
            mode=mode,
            status="completed",
            case_id=case_id,
            force=force,
            reconcile_result=result,
            report_id=report_id,
            job_ids=[],
        )

    # Async partner path: optional document re-queue for full mode, then reconcile + report
    job_ids: list[uuid.UUID] = []
    if mode == "full" and req.reprocess_documents:
        case_docs = list(
            (
                await session.execute(
                    select(CaseDocument).where(CaseDocument.case_id == case_id)
                )
            )
            .scalars()
            .all()
        )
        for cd in case_docs:
            doc = await session.get(Document, cd.document_id)
            if doc is None or doc.upload_status != "ready":
                continue
            job = ProcessingJob(
                id=uuid.uuid4(),
                tenant_id=auth.tenant_id,
                case_id=case_id,
                document_id=doc.id,
                status="queued",
                processing_version=doc.processing_version,
            )
            session.add(job)
            await session.flush()
            job_ids.append(job.id)
        await session.commit()
        from apps.api.queue import enqueue_process_document

        for jid in job_ids:
            try:
                await enqueue_process_document(jid)
            except Exception as exc:
                log.warning("analyze_enqueue_doc_failed", job_id=str(jid), error=str(exc))

    await enqueue_analyze_case(
        case_id,
        force=force,
        generate_report=req.generate_report,
        actor=str(auth.api_key_id),
    )
    return AnalyzeResponse(
        mode=mode,
        status="queued",
        case_id=case_id,
        force=force,
        reconcile_result=None,
        report_id=None,
        job_ids=job_ids,
    )


@router.get("/cases/{case_id}/report")
async def get_case_report(
    case_id: uuid.UUID,
    format: Literal["json", "pdf"] = Query(default="json"),
    regenerate: bool = Query(default=False),
    auth: AuthContext = Depends(require_scopes("reports:read")),
    session: AsyncSession = Depends(get_session),
):
    """Return the latest DD memo from the verified truth layer (JSON) or PDF bytes."""
    await _assert_case_access(session, case_id, auth.tenant_id)

    artifact = await get_latest_report(session, case_id=case_id, tenant_id=auth.tenant_id)
    needs_regen = (
        regenerate
        or artifact is None
        or artifact.status in ("STALE", "SUPERSEDED")
    )
    if needs_regen:
        if not auth.has_scope("reports:export") and not auth.has_scope("analyze") and not auth.has_scope("*"):
            if artifact is None or artifact.status != "READY":
                raise HTTPException(
                    status_code=404,
                    detail="No ready report; regenerate requires reports:export or analyze scope",
                )
        else:
            artifact = await generate_case_report(
                session,
                case_id=case_id,
                tenant_id=auth.tenant_id,
                triggered_by="api.get_report",
                rebuild_reason="api_regenerate" if regenerate else "missing_or_stale",
                actor=str(auth.api_key_id),
            )

    assert artifact is not None

    # Audit every export
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=auth.tenant_id,
            case_id=case_id,
            actor=str(auth.api_key_id),
            action="report.export",
            resource_type="report_artifact",
            resource_id=artifact.report_id,
            details={"format": format, "version": artifact.version},
        )
    )
    await session.commit()

    if format == "pdf":
        pdf_bytes: bytes | None = None
        if artifact.pdf_storage_uri:
            try:
                storage = get_storage()
                uri = artifact.pdf_storage_uri
                if uri.startswith("fs://"):
                    key = uri[len("fs://") :]
                    pdf_bytes = storage.get_bytes(key)
                elif uri.startswith("s3://"):
                    parts = uri.split("/", 3)
                    key = parts[3] if len(parts) > 3 else ""
                    if key:
                        pdf_bytes = storage.get_bytes(key)
            except Exception as exc:
                log.warning("report_pdf_load_failed", error=str(exc))
        if pdf_bytes is None:
            pdf_bytes = render_report_pdf(artifact.body or {}, title=artifact.title)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{artifact.report_id}.pdf"',
                "X-PIE-Report-Id": artifact.report_id,
                "X-PIE-Truth-Fingerprint": artifact.truth_fingerprint,
            },
        )

    return _report_out(artifact)


class GenerateReportQueued(BaseModel):
    queued: bool = True
    case_id: uuid.UUID


@router.post("/cases/{case_id}/report/generate")
async def generate_report_endpoint(
    case_id: uuid.UUID,
    sync: bool = Query(default=True),
    auth: AuthContext = Depends(require_scopes("reports:export")),
    session: AsyncSession = Depends(get_session),
):
    await _assert_case_access(session, case_id, auth.tenant_id)
    if sync:
        artifact = await generate_case_report(
            session,
            case_id=case_id,
            tenant_id=auth.tenant_id,
            triggered_by="api.generate",
            rebuild_reason="manual",
            actor=str(auth.api_key_id),
        )
        return _report_out(artifact)
    await enqueue_generate_report(case_id, actor=str(auth.api_key_id))
    return GenerateReportQueued(queued=True, case_id=case_id)


@router.get("/cases/{case_id}/reports", response_model=list[ReportExportOut])
async def list_reports(
    case_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    auth: AuthContext = Depends(require_scopes("reports:read")),
    session: AsyncSession = Depends(get_session),
) -> list[ReportExportOut]:
    await _assert_case_access(session, case_id, auth.tenant_id)
    rows = list(
        (
            await session.execute(
                select(ReportArtifact)
                .where(
                    ReportArtifact.case_id == case_id,
                    ReportArtifact.tenant_id == auth.tenant_id,
                )
                .order_by(ReportArtifact.version.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        ReportExportOut(
            report_id=r.report_id,
            version=r.version,
            status=r.status,
            truth_fingerprint=r.truth_fingerprint,
            reused_sections=list(r.reused_sections or []),
            rebuild_reason=r.rebuild_reason,
            created_at=r.created_at,
            title=r.title,
        )
        for r in rows
    ]
