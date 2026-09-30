# PIE — Property Intelligence Engine

Due-diligence document intelligence (Phase 0–8): multi-tenant cases, immutable document storage, durable jobs, evidence spine, structured facts, cross-doc reconciliation, case memory, evidence-grounded chat, human review, title/property domain engines, diligence reports, partner APIs, cost budgets, golden eval gates, and selective reprocess.

## Quick start

### 1. Infrastructure

```bash
cp .env.example .env
docker compose -f infrastructure/docker-compose.yml up -d
```

Postgres is published on **host port 55432** (avoids clashing with a local Postgres on 5432). Redis uses 6379. Local document storage defaults to `STORAGE_BACKEND=fs` under `./data/storage`. Optional MinIO: `docker compose -f infrastructure/docker-compose.yml --profile s3 up -d`.

### 2. Python env

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### 3. Migrate + seed

```bash
alembic upgrade head
pie-seed
# prints X-API-Key (default from .env: pie_dev_key_change_me)
```

### 4. Run API + worker

```bash
# terminal A
uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000

# terminal B
arq apps.worker.main.WorkerSettings
```

### 5. Web UI

```bash
cd apps/web && npm install && npm run dev
```

Open http://localhost:5173 — paste the API key, create a case, upload a PDF. After the job succeeds, open **Why?** / **Confirm**, or ask in **Case chat**.

## Sample curl

```bash
export PIE_KEY=pie_dev_key_change_me

curl -s http://localhost:8000/health

curl -s -X POST http://localhost:8000/v1/cases \
  -H "X-API-Key: $PIE_KEY" -H "Content-Type: application/json" \
  -d '{"title":"Plot 183/2 diligence"}'

# upload (replace CASE_ID)
curl -s -X POST "http://localhost:8000/v1/cases/CASE_ID/documents/complete" \
  -H "X-API-Key: $PIE_KEY" \
  -F "file=@./sample.pdf" -F "role=primary"

# facts / chat / risk (after job succeeds)
curl -s "http://localhost:8000/v1/cases/CASE_ID/facts" -H "X-API-Key: $PIE_KEY"
curl -s "http://localhost:8000/v1/cases/CASE_ID/intelligence" -H "X-API-Key: $PIE_KEY"
curl -s "http://localhost:8000/v1/cases/CASE_ID/risk" -H "X-API-Key: $PIE_KEY"
curl -s -X POST "http://localhost:8000/v1/cases/CASE_ID/chat" \
  -H "X-API-Key: $PIE_KEY" -H "Content-Type: application/json" \
  -d '{"message":"Who is the current owner?"}'
curl -s -X POST "http://localhost:8000/v1/cases/CASE_ID/facts/FACT_ID/confirm" \
  -H "X-API-Key: $PIE_KEY" -H "Content-Type: application/json" -d '{}'

# partner path: analyze → report (no UI)
curl -s -X POST "http://localhost:8000/v1/cases/CASE_ID/analyze" \
  -H "X-API-Key: $PIE_KEY" -H "Content-Type: application/json" \
  -d '{"mode":"incremental","sync":true,"generate_report":true}'

curl -s "http://localhost:8000/v1/cases/CASE_ID/report" -H "X-API-Key: $PIE_KEY"
curl -s "http://localhost:8000/v1/cases/CASE_ID/report?format=pdf" -H "X-API-Key: $PIE_KEY" -o report.pdf

# Phase 8: ops + selective reprocess
curl -s "http://localhost:8000/v1/ops/summary" -H "X-API-Key: $PIE_KEY"
curl -s "http://localhost:8000/v1/ops/costs" -H "X-API-Key: $PIE_KEY"
curl -s -X POST "http://localhost:8000/v1/cases/CASE_ID/reprocess" \
  -H "X-API-Key: $PIE_KEY" -H "Content-Type: application/json" \
  -d '{"mode":"prompt_bump","force":true}'
```

## Architecture (Phase 0–8)

- **API** (`apps/api`): cases, uploads, jobs, evidence/pages, facts, intelligence, chat/memory, review queue, risk/findings, analyze, reports, ops/costs, reprocess
- **Worker** (`apps/worker`): arq consumer with retries; pipeline stages + reconciliation + reports
- **Pipeline**: `integrity → page_split → page_quality → classify → ocr` (dual OCR on critical pages → `CONFLICTING_OCR` when A/B diverge) `→ evidence_persist → structured_extract → index_embeddings` (+ case `reconcile` → review sync → domain engines); versioned stages for selective reprocess
- **Domain engines** (`packages/dd`): temporal ownership, share accounting, survey/parcel identity, area reconciliation, legal Layer-1 rules + Layer-2 Pro narrative (Gemini when keyed), versioned risk, multi-dimension confidence; **GIS / external registry still hooks only**
- **Reports** (`packages/reports`): DD memo from verified truth layer (JSON + PDF); section-level reuse after review decisions
- **Retrieval** (`packages/retrieval`): hybrid RAG + **pgvector** (768-d) + answer contract + guardrails + review decisions / compounding
- **Eval / ops** (`packages/eval`, `packages/ops`): golden/adversarial packs, citation/conflict gates, cost budgets, ops summary
- **Storage**: MinIO (S3) or `STORAGE_BACKEND=fs` (paths prefixed `tenants/{tenant_id}/…`)
- **AI**: Flash by default; Pro for ownership/legal Layer-2; `GeminiProvider` when `GEMINI_API_KEY` is set; otherwise `MockProvider`
- **Auth**: `users` linked to API keys / case members; scopes (`*` or granular); export actions audited; PII redacted in logs

After upgrading past migration `010_pgvector`, recreate or re-index embeddings (`reprocess` / re-run `index_embeddings`) — prior JSONB vectors are wiped. Compose Postgres image is `pgvector/pgvector:pg16` (recreate the volume if you were on plain `postgres:16`).

## Tests

```bash
pytest tests/test_unit.py tests/test_idempotency.py tests/test_phase2.py tests/test_phase3.py tests/test_phase4.py tests/test_phase5.py tests/test_phase6.py tests/test_phase7.py tests/test_phase8.py -q
# with Compose up + migrated DB:
pytest tests/test_api.py -q
```

## Phase exit criteria

**Phase 0:** upload PDF → stored immutably → job succeeds; duplicate hash dedupes; cross-tenant denied.

**Phase 1:** every page yields evidence rows; integrity warnings persisted; re-run OCR with same versions skips; UI shows page + evidence highlight; critical pages run dual OCR and persist `CONFLICTING_OCR` when passes diverge.

**Phase 2:** sale deed yields owner/buyer/seller/survey/area/date facts with evidence IDs; UI “Why?” opens cited pages; no EXTRACTED fact without an evidence link (NOT_FOUND allowed without links).

**Phase 3:** conflicting deeds produce OPEN conflicts; document graph shows missing instruments; scorecard + open conflict count on case overview.

**Phase 4:** case chat returns status + citations; survey ID queries hit exact ID path first; confirming a fact upgrades to VERIFIED and chat prefers it; new uploads index embeddings for that case only.

**Phase 5:** review queue filterable by severity/case/type; approve/reject/merge/split/request_docs/annotate update facts + conflicts + memory without re-OCR; audit trail records who changed what, when, why; resolutions survive re-reconcile.

**Phase 6:** ownership chain timeline from events; mortgage-without-release yields `UNRESOLVED` + missing release (never “clear”).

**Phase 7:** report from verified layer (JSON/PDF); regeneration after review reuses unchanged sections; partner upload → analyze → report without UI; chat and report agree on conflict status; API key scopes + export audit + PII log redaction.

**Phase 8:** cost budget alerts per tenant; golden/adversarial eval CI gate; selective reprocess after prompt bumps without full corpus downtime; ops summary (cost/latency/OCR/review/verified ratio); stage timeouts + worker retries; AI fail → REQUIRES_REVIEW / INSUFFICIENT_EVIDENCE.
