"""API integration tests (requires Postgres + Redis from Compose, or skip)."""

from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

# Force mock LLM + FS storage for tests
os.environ.setdefault("STORAGE_BACKEND", "fs")
os.environ.setdefault("FS_STORAGE_PATH", "./data/test_storage")
os.environ.setdefault("GEMINI_API_KEY", "")
os.environ.setdefault("PIE_API_KEY", "pie_test_key_integration")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.asyncio
async def test_health():
    from packages.config.settings import get_settings

    get_settings.cache_clear()
    from apps.api.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_tenant_isolation_and_case_flow():
    """Requires migrated DB + seeded key. Skips if DB unreachable."""
    from packages.config.settings import get_settings

    get_settings.cache_clear()

    try:
        from packages.domain.db import get_engine
        from sqlalchemy import text

        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Database not available: {exc}")

    # seed
    from apps.api.seed import seed

    api_key = await seed()

    from apps.api.main import create_app

    app = create_app()
    headers = {"X-API-Key": api_key}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # unauthorized
        r = await client.get("/v1/cases")
        assert r.status_code == 401

        # create case
        r = await client.post(
            "/v1/cases",
            headers=headers,
            json={"title": f"Test {uuid.uuid4().hex[:8]}"},
        )
        assert r.status_code == 200
        case_id = r.json()["id"]

        # upload tiny pdf
        # Minimal valid-ish PDF
        pdf = b"""%PDF-1.4
1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj
2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj
3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj
4 0 obj<< /Length 44 >>stream
BT /F1 12 Tf 100 100 Td (Sale Deed Gata 183/2) Tj ET
endstream
endobj
5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000266 00000 n 
0000000362 00000 n 
trailer<< /Size 6 /Root 1 0 R >>
startxref
441
%%EOF
"""
        files = {"file": ("sale_deed.pdf", pdf, "application/pdf")}
        r = await client.post(
            f"/v1/cases/{case_id}/documents/complete",
            headers=headers,
            files=files,
            data={"role": "primary"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["document"]["content_hash"]
        job_id = body["job_id"]
        doc_id = body["document"]["id"]

        # dedupe second upload
        r2 = await client.post(
            f"/v1/cases/{case_id}/documents/complete",
            headers=headers,
            files={"file": ("sale_deed_copy.pdf", pdf, "application/pdf")},
            data={"role": "supporting"},
        )
        assert r2.status_code == 200
        assert r2.json()["deduped"] is True
        assert r2.json()["document"]["id"] == doc_id

        # job endpoint tenant scoped
        r = await client.get(f"/v1/jobs/{job_id}", headers=headers)
        assert r.status_code == 200

        # wrong key cannot see
        r = await client.get(f"/v1/jobs/{job_id}", headers={"X-API-Key": "not-a-real-key"})
        assert r.status_code == 401
