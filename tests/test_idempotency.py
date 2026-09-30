"""Idempotent stage skip logic."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from packages.domain.models import ProcessingStageRun


@pytest.mark.asyncio
async def test_stage_idempotency_key_fields():
    """Ensure stage run unique key uses empty strings not None."""
    run = ProcessingStageRun(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_hash="abc",
        stage_name="integrity",
        stage_version="1",
        model_version="",
        prompt_version="",
        status="succeeded",
        skipped=False,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    assert run.model_version == ""
    assert run.prompt_version == ""
