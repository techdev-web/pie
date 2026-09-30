from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import AuthContext, require_auth
from apps.api.schemas import DocumentCompleteOut, DocumentInitiate, DocumentInitiateOut, DocumentOut
from apps.api.queue import enqueue_process_document
from packages.config import get_settings
from packages.domain.db import get_session
from packages.domain.models import (
    AuditEvent,
    Case,
    CaseDocument,
    Document,
    ProcessingJob,
)
from packages.domain.storage import document_storage_key, get_storage, sha256_bytes

router = APIRouter(tags=["documents"])


async def _get_case(
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


@router.post("/cases/{case_id}/documents", response_model=DocumentInitiateOut)
async def initiate_document(
    case_id: uuid.UUID,
    body: DocumentInitiate,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> DocumentInitiateOut:
    await _get_case(session, case_id, auth.tenant_id)
    # Multipart upload via complete endpoint; initiate returns a pending placeholder
    # when content hash is unknown yet. Client should use upload-and-complete.
    doc_id = uuid.uuid4()
    doc = Document(
        id=doc_id,
        tenant_id=auth.tenant_id,
        content_hash=f"pending:{doc_id}",  # replaced on upload
        mime_type=body.content_type,
        size_bytes=0,
        storage_uri="",
        source_filename=body.filename,
        upload_status="pending",
        processing_version=get_settings().processing_version,
    )
    session.add(doc)
    session.add(
        CaseDocument(
            id=uuid.uuid4(),
            case_id=case_id,
            document_id=doc.id,
            tenant_id=auth.tenant_id,
            role=body.role if body.role in ("primary", "supporting") else "supporting",
        )
    )
    await session.commit()
    return DocumentInitiateOut(
        document_id=doc.id,
        upload_mode="multipart",
        upload_url=f"/v1/cases/{case_id}/documents/{doc.id}/upload",
        message="POST multipart file to upload_url, then processing starts automatically.",
    )


@router.post(
    "/cases/{case_id}/documents/{document_id}/upload",
    response_model=DocumentCompleteOut,
)
async def upload_document(
    case_id: uuid.UUID,
    document_id: uuid.UUID,
    file: UploadFile = File(...),
    role: str = Form(default="supporting"),
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> DocumentCompleteOut:
    await _get_case(session, case_id, auth.tenant_id)

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
        raise HTTPException(status_code=404, detail="Document not linked to case")

    pending = await session.get(Document, document_id)
    if pending is None or pending.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=404, detail="Document not found")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    content_hash = sha256_bytes(data)
    storage = get_storage()
    key = document_storage_key(str(auth.tenant_id), content_hash)

    # Dedup within tenant
    existing = (
        await session.execute(
            select(Document).where(
                Document.tenant_id == auth.tenant_id,
                Document.content_hash == content_hash,
                Document.upload_status == "stored",
            )
        )
    ).scalar_one_or_none()

    deduped = False
    if existing is not None:
        deduped = True
        # Retarget case_document to existing immutable doc
        link.document_id = existing.id
        if role in ("primary", "supporting"):
            link.role = role
        # Remove empty pending row if different
        if pending.id != existing.id:
            await session.delete(pending)
        document = existing
        # Ensure case link uniqueness — may already exist
        other = (
            await session.execute(
                select(CaseDocument).where(
                    CaseDocument.case_id == case_id,
                    CaseDocument.document_id == existing.id,
                )
            )
        ).scalars().all()
        # If duplicate links, keep one
        if len(other) > 1:
            for extra in other[1:]:
                await session.delete(extra)
    else:
        uri = storage.put_bytes(key, data, content_type=file.content_type or pending.mime_type)
        pending.content_hash = content_hash
        pending.size_bytes = len(data)
        pending.storage_uri = uri
        pending.mime_type = file.content_type or pending.mime_type or "application/pdf"
        pending.source_filename = file.filename or pending.source_filename
        pending.upload_status = "stored"
        if role in ("primary", "supporting"):
            link.role = role
        document = pending

    job = ProcessingJob(
        id=uuid.uuid4(),
        tenant_id=auth.tenant_id,
        case_id=case_id,
        document_id=document.id,
        status="queued",
        processing_version=get_settings().processing_version,
    )
    session.add(job)
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=auth.tenant_id,
            case_id=case_id,
            actor=str(auth.api_key_id),
            action="document.upload",
            resource_type="document",
            resource_id=str(document.id),
            details={"deduped": deduped, "content_hash": content_hash},
        )
    )
    await session.commit()
    await enqueue_process_document(job.id)

    return DocumentCompleteOut(
        document=DocumentOut.model_validate(document),
        job_id=job.id,
        deduped=deduped,
    )


@router.post(
    "/cases/{case_id}/documents/complete",
    response_model=DocumentCompleteOut,
)
async def upload_and_complete(
    case_id: uuid.UUID,
    file: UploadFile = File(...),
    role: str = Form(default="supporting"),
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> DocumentCompleteOut:
    """Convenience: create + upload + enqueue in one call."""
    await _get_case(session, case_id, auth.tenant_id)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    content_hash = sha256_bytes(data)
    existing = (
        await session.execute(
            select(Document).where(
                Document.tenant_id == auth.tenant_id,
                Document.content_hash == content_hash,
                Document.upload_status == "stored",
            )
        )
    ).scalar_one_or_none()

    deduped = existing is not None
    if existing:
        document = existing
    else:
        storage = get_storage()
        key = document_storage_key(str(auth.tenant_id), content_hash)
        uri = storage.put_bytes(
            key, data, content_type=file.content_type or "application/pdf"
        )
        document = Document(
            id=uuid.uuid4(),
            tenant_id=auth.tenant_id,
            content_hash=content_hash,
            mime_type=file.content_type or "application/pdf",
            size_bytes=len(data),
            storage_uri=uri,
            source_filename=file.filename,
            upload_status="stored",
            processing_version=get_settings().processing_version,
        )
        session.add(document)
        await session.flush()

    link = (
        await session.execute(
            select(CaseDocument).where(
                CaseDocument.case_id == case_id,
                CaseDocument.document_id == document.id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        session.add(
            CaseDocument(
                id=uuid.uuid4(),
                case_id=case_id,
                document_id=document.id,
                tenant_id=auth.tenant_id,
                role=role if role in ("primary", "supporting") else "supporting",
            )
        )

    job = ProcessingJob(
        id=uuid.uuid4(),
        tenant_id=auth.tenant_id,
        case_id=case_id,
        document_id=document.id,
        status="queued",
        processing_version=get_settings().processing_version,
    )
    session.add(job)
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=auth.tenant_id,
            case_id=case_id,
            actor=str(auth.api_key_id),
            action="document.upload_complete",
            resource_type="document",
            resource_id=str(document.id),
            details={"deduped": deduped, "content_hash": content_hash},
        )
    )
    await session.commit()
    await enqueue_process_document(job.id)

    return DocumentCompleteOut(
        document=DocumentOut.model_validate(document),
        job_id=job.id,
        deduped=deduped,
    )
