# PIE — Property Intelligence Engine

Due-diligence document intelligence (Phase 0 + Phase 1): multi-tenant cases, immutable document storage, durable jobs, and an evidence spine powered by Gemini (or MockProvider).

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

Open http://localhost:5173 — paste the API key, create a case, upload a PDF.

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
```

## Architecture (Phase 0–1)

- **API** (`apps/api`): cases, uploads, jobs, evidence/pages
- **Worker** (`apps/worker`): arq consumer running pipeline stages
- **Pipeline**: `integrity → page_split → page_quality → classify → ocr → evidence_persist`
- **Storage**: MinIO (S3) or `STORAGE_BACKEND=fs`
- **AI**: `GeminiProvider` when `GEMINI_API_KEY` is set; otherwise `MockProvider`

## Tests

```bash
pytest tests/test_unit.py tests/test_idempotency.py -q
# with Compose up + migrated DB:
pytest tests/test_api.py -q
```

## Phase exit criteria

**Phase 0:** upload PDF → stored immutably → job succeeds; duplicate hash dedupes; cross-tenant denied.

**Phase 1:** every page yields evidence rows; integrity warnings persisted; re-run OCR with same versions skips; UI shows page + evidence highlight.
