"""Index embeddings after structured extraction (Phase 4)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models import Document, ProcessingJob
from packages.retrieval.embeddings import index_case_embeddings


async def stage_index_embeddings(
    session: AsyncSession, job: ProcessingJob, document: Document
) -> None:
    await index_case_embeddings(
        session,
        case_id=job.case_id,
        tenant_id=job.tenant_id,
        document_id=document.id,
    )
