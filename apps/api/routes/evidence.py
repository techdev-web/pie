from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import AuthContext, require_auth
from apps.api.schemas import EvidenceOut, PageOut
from packages.domain.db import get_session
from packages.domain.models import Case, CaseDocument, Document, DocumentPage, EvidenceItem
from packages.domain.storage import get_storage

router = APIRouter(tags=["evidence"])


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


@router.get("/cases/{case_id}/documents/{document_id}/evidence", response_model=list[EvidenceOut])
async def list_evidence(
    case_id: uuid.UUID,
    document_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[EvidenceItem]:
    await _assert_case_access(session, case_id, auth.tenant_id)
    link = (
        await session.execute(
            select(CaseDocument).where(
                CaseDocument.case_id == case_id,
                CaseDocument.document_id == document_id,
                CaseDocument.tenant_id == auth.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="Document not in case")

    result = await session.execute(
        select(EvidenceItem)
        .where(
            EvidenceItem.case_id == case_id,
            EvidenceItem.document_id == document_id,
            EvidenceItem.tenant_id == auth.tenant_id,
        )
        .order_by(EvidenceItem.page_number)
    )
    return list(result.scalars().all())


@router.get("/cases/{case_id}/documents/{document_id}/pages", response_model=list[PageOut])
async def list_pages(
    case_id: uuid.UUID,
    document_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[PageOut]:
    await _assert_case_access(session, case_id, auth.tenant_id)
    doc = await session.get(Document, document_id)
    if doc is None or doc.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=404, detail="Document not found")

    pages = (
        await session.execute(
            select(DocumentPage)
            .where(DocumentPage.document_id == document_id)
            .order_by(DocumentPage.page_number)
        )
    ).scalars().all()

    return [
        PageOut(
            id=p.id,
            page_number=p.page_number,
            width=p.width,
            height=p.height,
            image_url=f"/v1/cases/{case_id}/documents/{document_id}/pages/{p.page_number}/image",
            has_text_layer=p.has_text_layer,
            quality_label=p.quality_label,
            ocr_route=p.ocr_route,
        )
        for p in pages
    ]


@router.get("/cases/{case_id}/documents/{document_id}/pages/{page_number}/image")
async def get_page_image(
    case_id: uuid.UUID,
    document_id: uuid.UUID,
    page_number: int,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _assert_case_access(session, case_id, auth.tenant_id)
    page = (
        await session.execute(
            select(DocumentPage).where(
                DocumentPage.document_id == document_id,
                DocumentPage.page_number == page_number,
                DocumentPage.tenant_id == auth.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if page is None or not page.image_storage_uri:
        raise HTTPException(status_code=404, detail="Page image not found")

    storage = get_storage()
    uri = page.image_storage_uri
    if uri.startswith("s3://"):
        without = uri[len("s3://") :]
        _, _, key = without.partition("/")
        data = storage.get_bytes(key)
    elif uri.startswith("fs://"):
        data = storage.get_bytes(uri[len("fs://") :])
    else:
        data = storage.get_bytes(uri)
    return Response(content=data, media_type="image/png")
