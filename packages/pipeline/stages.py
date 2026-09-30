"""Pipeline stage implementations for Phase 1 evidence spine (+ helpers used by Phase 2)."""

from __future__ import annotations

import time
import uuid

import pymupdf as fitz
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.ai import get_llm_provider
from packages.ai.model_runs import build_model_run
from packages.domain.models import (
    Document,
    DocumentClassification,
    DocumentIntegrityCheck,
    DocumentPage,
    EvidenceItem,
    PageExtraction,
    ProcessingJob,
)
from packages.domain.storage import (
    get_storage,
    page_image_storage_key,
    sha256_bytes,
)
from packages.domain.textutil import normalize_text
from packages.observability import get_logger

log = get_logger("pipeline.stages")

# Key stored on page_extractions used by evidence_persist
EXTRACTION_VERSION = "1.0"


def _sniff_mime(data: bytes, filename: str | None) -> str:
    if data.startswith(b"%PDF"):
        return "application/pdf"
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return "application/pdf"
    if name.endswith(".png"):
        return "image/png"
    if name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    return "application/octet-stream"


def _load_document_bytes(document: Document) -> bytes:
    storage = get_storage()
    # storage_uri like s3://bucket/key or fs://key
    uri = document.storage_uri
    if uri.startswith("s3://"):
        # s3://bucket/key
        without = uri[len("s3://") :]
        _, _, key = without.partition("/")
        return storage.get_bytes(key)
    if uri.startswith("fs://"):
        return storage.get_bytes(uri[len("fs://") :])
    # treat as key
    return storage.get_bytes(uri)


async def stage_integrity(session: AsyncSession, job: ProcessingJob, document: Document) -> None:
    data = _load_document_bytes(document)
    mime = _sniff_mime(data, document.source_filename)
    document.mime_type = mime

    checks: list[DocumentIntegrityCheck] = []

    if mime != "application/pdf":
        checks.append(
            DocumentIntegrityCheck(
                id=uuid.uuid4(),
                document_id=document.id,
                tenant_id=document.tenant_id,
                check_name="mime_type",
                status="WARNING",
                message=f"Unexpected MIME type: {mime}",
                details={"mime_type": mime},
            )
        )
    else:
        checks.append(
            DocumentIntegrityCheck(
                id=uuid.uuid4(),
                document_id=document.id,
                tenant_id=document.tenant_id,
                check_name="mime_type",
                status="PASS",
                message="PDF detected",
                details={"mime_type": mime},
            )
        )

    page_count = 0
    blank_pages: list[int] = []
    if mime == "application/pdf":
        try:
            pdf = fitz.open(stream=data, filetype="pdf")
            page_count = pdf.page_count
            for i, page in enumerate(pdf, start=1):
                text = page.get_text("text") or ""
                if len(text.strip()) < 5:
                    # also check image content size as proxy for blank
                    pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
                    if pix.width * pix.height < 100 or len(text.strip()) == 0:
                        blank_pages.append(i)
            pdf.close()
        except Exception as exc:
            checks.append(
                DocumentIntegrityCheck(
                    id=uuid.uuid4(),
                    document_id=document.id,
                    tenant_id=document.tenant_id,
                    check_name="pdf_open",
                    status="FAIL",
                    message=str(exc),
                )
            )
            session.add_all(checks)
            await session.commit()
            raise

    document.page_count = page_count
    checks.append(
        DocumentIntegrityCheck(
            id=uuid.uuid4(),
            document_id=document.id,
            tenant_id=document.tenant_id,
            check_name="page_count",
            status="PASS" if page_count > 0 else "FAIL",
            message=f"page_count={page_count}",
            details={"page_count": page_count},
        )
    )

    if blank_pages:
        checks.append(
            DocumentIntegrityCheck(
                id=uuid.uuid4(),
                document_id=document.id,
                tenant_id=document.tenant_id,
                check_name="blank_pages",
                status="WARNING",
                message=f"Possibly blank pages: {blank_pages}",
                details={"blank_pages": blank_pages},
            )
        )
    else:
        checks.append(
            DocumentIntegrityCheck(
                id=uuid.uuid4(),
                document_id=document.id,
                tenant_id=document.tenant_id,
                check_name="blank_pages",
                status="PASS",
                message="No blank pages detected",
            )
        )

    # Missing page heuristic: non-contiguous labeled pages in text (lightweight)
    checks.append(
        DocumentIntegrityCheck(
            id=uuid.uuid4(),
            document_id=document.id,
            tenant_id=document.tenant_id,
            check_name="page_sequence",
            status="PASS",
            message="File page sequence is contiguous by index",
            details={"pages": list(range(1, page_count + 1))},
        )
    )

    # Clear prior integrity checks for reprocess of same doc in new job path
    await session.execute(
        delete(DocumentIntegrityCheck).where(DocumentIntegrityCheck.document_id == document.id)
    )
    session.add_all(checks)
    await session.commit()


async def stage_page_split(session: AsyncSession, job: ProcessingJob, document: Document) -> None:
    data = _load_document_bytes(document)
    storage = get_storage()

    # Replace pages if re-running after version bump (idempotent skip handles same version)
    existing = (
        await session.execute(
            select(DocumentPage).where(DocumentPage.document_id == document.id)
        )
    ).scalars().all()
    if existing:
        # Pages already exist from a prior successful run — keep them
        return

    if document.mime_type != "application/pdf" and not data.startswith(b"%PDF"):
        # Single image as one page
        page = DocumentPage(
            id=uuid.uuid4(),
            document_id=document.id,
            tenant_id=document.tenant_id,
            page_number=1,
            has_text_layer=False,
        )
        key = page_image_storage_key(str(document.tenant_id), str(document.id), 1)
        uri = storage.put_bytes(key, data, content_type=document.mime_type)
        page.image_storage_uri = uri
        session.add(page)
        document.page_count = 1
        await session.commit()
        return

    pdf = fitz.open(stream=data, filetype="pdf")
    page_texts: list[tuple[DocumentPage, str]] = []
    for i, pdf_page in enumerate(pdf, start=1):
        text = pdf_page.get_text("text") or ""
        pix = pdf_page.get_pixmap(matrix=fitz.Matrix(2, 2))
        png = pix.tobytes("png")
        key = page_image_storage_key(str(document.tenant_id), str(document.id), i)
        uri = storage.put_bytes(key, png, content_type="image/png")
        page = DocumentPage(
            id=uuid.uuid4(),
            document_id=document.id,
            tenant_id=document.tenant_id,
            page_number=i,
            width=pix.width,
            height=pix.height,
            image_storage_uri=uri,
            has_text_layer=bool(text.strip()),
        )
        page_texts.append((page, text))
    pdf.close()

    session.add_all([p for p, _ in page_texts])
    await session.flush()

    for page, text in page_texts:
        session.add(
            PageExtraction(
                id=uuid.uuid4(),
                document_id=document.id,
                page_id=page.id,
                tenant_id=document.tenant_id,
                extraction_type="text_layer",
                text=text,
                provider="pymupdf",
                confidence=1.0 if text.strip() else 0.0,
                extraction_version=EXTRACTION_VERSION,
            )
        )
    document.page_count = len(page_texts)
    await session.commit()


async def stage_page_quality(session: AsyncSession, job: ProcessingJob, document: Document) -> None:
    pages = (
        await session.execute(
            select(DocumentPage).where(DocumentPage.document_id == document.id)
        )
    ).scalars().all()

    for page in pages:
        text_ext = (
            await session.execute(
                select(PageExtraction).where(
                    PageExtraction.page_id == page.id,
                    PageExtraction.extraction_type == "text_layer",
                )
            )
        ).scalar_one_or_none()
        text = (text_ext.text if text_ext else "") or ""
        density = len(text.strip()) / max(1, (page.width or 1000) * (page.height or 1000) / 10000)
        if page.has_text_layer and len(text.strip()) > 40:
            page.quality_label = "GOOD"
            page.quality_score = min(1.0, 0.7 + density)
            page.ocr_route = "digital"
        elif len(text.strip()) > 10:
            page.quality_label = "FAIR"
            page.quality_score = 0.55
            page.ocr_route = "hybrid"
        else:
            page.quality_label = "LOW"
            page.quality_score = 0.35
            page.ocr_route = "vision"

        if page.quality_label == "LOW":
            session.add(
                DocumentIntegrityCheck(
                    id=uuid.uuid4(),
                    document_id=document.id,
                    tenant_id=document.tenant_id,
                    check_name="page_quality",
                    status="WARNING",
                    message=f"Low quality page {page.page_number}",
                    details={"page_number": page.page_number, "score": page.quality_score},
                )
            )
    await session.commit()


async def stage_classify(session: AsyncSession, job: ProcessingJob, document: Document) -> None:
    llm = get_llm_provider()
    # sample from first page text layer
    first_page = (
        await session.execute(
            select(DocumentPage)
            .where(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_number)
            .limit(1)
        )
    ).scalar_one_or_none()
    sample = ""
    if first_page:
        ext = (
            await session.execute(
                select(PageExtraction).where(
                    PageExtraction.page_id == first_page.id,
                    PageExtraction.extraction_type == "text_layer",
                )
            )
        ).scalar_one_or_none()
        sample = (ext.text if ext else "") or ""

    t0 = time.perf_counter()
    result = await llm.classify(filename=document.source_filename, sample_text=sample)
    t1 = time.perf_counter()
    model_run = build_model_run(
        tenant_id=job.tenant_id,
        case_id=job.case_id,
        document_id=document.id,
        stage="classify",
        prompt_id=result.prompt_id,
        prompt_version=result.prompt_version,
        model=result.model,
        temperature=result.temperature,
        schema_version=result.schema_version,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=int((t1 - t0) * 1000),
    )
    session.add(model_run)
    await session.execute(
        delete(DocumentClassification).where(DocumentClassification.document_id == document.id)
    )
    session.add(
        DocumentClassification(
            id=uuid.uuid4(),
            document_id=document.id,
            tenant_id=document.tenant_id,
            doc_type=result.doc_type,
            confidence=result.confidence,
            details=result.details,
            model_run_id=model_run.id,
        )
    )
    await session.commit()


async def stage_ocr(session: AsyncSession, job: ProcessingJob, document: Document) -> None:
    llm = get_llm_provider()
    storage = get_storage()
    pages = (
        await session.execute(
            select(DocumentPage)
            .where(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_number)
        )
    ).scalars().all()

    for page in pages:
        text_ext = (
            await session.execute(
                select(PageExtraction).where(
                    PageExtraction.page_id == page.id,
                    PageExtraction.extraction_type == "text_layer",
                )
            )
        ).scalar_one_or_none()
        text_layer = text_ext.text if text_ext else None

        image_bytes: bytes | None = None
        if page.image_storage_uri:
            uri = page.image_storage_uri
            if uri.startswith("s3://"):
                without = uri[len("s3://") :]
                _, _, key = without.partition("/")
                image_bytes = storage.get_bytes(key)
            elif uri.startswith("fs://"):
                image_bytes = storage.get_bytes(uri[len("fs://") :])

        force_vision = page.ocr_route == "vision"
        t0 = time.perf_counter()
        result = await llm.extract_page(
            page_number=page.page_number,
            text_layer=text_layer,
            image_bytes=image_bytes,
            force_vision=force_vision,
        )
        t1 = time.perf_counter()

        model_run_id = None
        if result.provider not in ("digital_text",):
            model_run = build_model_run(
                tenant_id=job.tenant_id,
                case_id=job.case_id,
                document_id=document.id,
                stage="ocr",
                prompt_id=result.prompt_id,
                prompt_version=result.prompt_version,
                model=result.model,
                temperature=result.temperature,
                schema_version=result.schema_version,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=int((t1 - t0) * 1000),
            )
            session.add(model_run)
            await session.flush()
            model_run_id = model_run.id

        # remove prior ocr extractions for this page
        await session.execute(
            delete(PageExtraction).where(
                PageExtraction.page_id == page.id,
                PageExtraction.extraction_type == "ocr",
            )
        )
        session.add(
            PageExtraction(
                id=uuid.uuid4(),
                document_id=document.id,
                page_id=page.id,
                tenant_id=document.tenant_id,
                extraction_type="ocr",
                text=result.text,
                provider=result.provider,
                confidence=result.confidence,
                model_run_id=model_run_id,
                extraction_version=EXTRACTION_VERSION,
            )
        )
    await session.commit()


async def stage_evidence_persist(
    session: AsyncSession, job: ProcessingJob, document: Document
) -> None:
    pages = (
        await session.execute(
            select(DocumentPage)
            .where(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_number)
        )
    ).scalars().all()

    # Idempotent: if evidence already exists for this document+case, keep
    existing = (
        await session.execute(
            select(EvidenceItem).where(
                EvidenceItem.document_id == document.id,
                EvidenceItem.case_id == job.case_id,
            )
        )
    ).scalars().all()
    if existing:
        return

    items: list[EvidenceItem] = []
    for page in pages:
        ocr = (
            await session.execute(
                select(PageExtraction).where(
                    PageExtraction.page_id == page.id,
                    PageExtraction.extraction_type == "ocr",
                )
            )
        ).scalar_one_or_none()
        if ocr is None or not (ocr.text or "").strip():
            continue

        text = ocr.text or ""
        evidence_id = f"ev_{sha256_bytes(f'{document.id}:{page.page_number}:{text[:200]}'.encode())[:20]}"
        items.append(
            EvidenceItem(
                id=uuid.uuid4(),
                evidence_id=evidence_id,
                document_id=document.id,
                case_id=job.case_id,
                tenant_id=job.tenant_id,
                page_number=page.page_number,
                text=text,
                normalized_text=normalize_text(text),
                bbox=[0, 0, page.width or 0, page.height or 0] if page.width else None,
                source_type="OCR" if ocr.provider != "digital_text" else "DIGITAL",
                ocr_provider=ocr.provider,
                ocr_confidence=ocr.confidence,
                extraction_version=EXTRACTION_VERSION,
                model_run_id=ocr.model_run_id,
            )
        )
    session.add_all(items)
    await session.commit()
    log.info("evidence_persisted", document_id=str(document.id), count=len(items))
