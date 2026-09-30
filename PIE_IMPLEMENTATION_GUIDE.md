# PIE — Due Diligence AI Implementation Guide

**Product:** PIE (Property Intelligence Engine)  
**Source architecture:** `AUXILIUM_AI_ARCHITECTURE_V2.md`  
**Primary AI provider:** Google Gemini (Flash for volume, Pro for high-stakes reasoning)  
**Purpose:** Build a durable, evidence-first due-diligence AI that ingests one or many documents, compounds intelligence over time, and answers with citations — not a one-shot chat wrapper.

------------------------------------------------------------------------

## 0. What PIE Is

PIE is a **case-centric due diligence workspace**. A user opens a case (deal, title pack, KYC pack, vendor DD pack), uploads documents over days or weeks, and PIE:

1. **Ingests** each document with integrity, OCR, and provenance.
2. **Extracts** candidate facts with evidence pointers (page, span, bbox).
3. **Normalizes & reconciles** facts across documents.
4. **Builds lasting intelligence** — entities, events, conflicts, missing docs, graphs.
5. **Lets the user chat / review** against that intelligence, with citations.
6. **Improves the knowledge base** when the user corrects, confirms, or uploads more.

### Product loop (how good agents actually get smarter)

```text
Upload / chat / review feedback
        ↓
Acquire evidence
        ↓
Propose facts (Gemini)
        ↓
Validate + reconcile (code + rules)
        ↓
Update knowledge graph + case memory
        ↓
Index for retrieval (hybrid)
        ↓
Answer / report with citations + uncertainty
        ↓
Human confirm / reject / annotate
        ↓
Write-back into verified facts (compounding intelligence)
```

PIE does **not** treat the LLM as source of truth. Gemini proposes; the **case knowledge layer** persists and compounds.

### Non-goals (carry forward from architecture)

- Do not claim legal title from an LLM answer alone.
- Do not invent identifiers, dates, owners, or encumbrance status.
- Do not equate “not found in uploads” with “does not exist.”
- Do not merge people/parcels aggressively.
- Do not replace qualified human review for high-risk findings.

------------------------------------------------------------------------

## 1. Product Primitives

| Primitive | Meaning |
|-----------|---------|
| **Workspace / Tenant** | Isolation boundary (partner, firm, org). |
| **Case** | One due-diligence matter (property, deal, entity). All intelligence is scoped to a case. |
| **Document** | Immutable uploaded file + pages + integrity metadata. |
| **Evidence** | Provenanced text/region from a page. |
| **Fact** | Normalized claim linked to evidence, with verification state. |
| **Entity** | Person, org, parcel, instrument, bank, etc. |
| **Event** | Dated ownership/legal/transaction event (not just a graph edge). |
| **Conflict** | First-class disagreement between facts. |
| **MissingEvidence** | Explicit gap (referenced but not uploaded). |
| **Graph** | Document graph + entity/event graph for the case. |
| **Memory** | Case memory: verified facts, user decisions, open questions, chat summaries. |
| **Conversation** | Evidence-grounded Q&A over the case truth layer. |
| **ReviewTask** | Human-in-the-loop item for ambiguous/high-risk items. |
| **Report** | TSR / DD memo generated from the verified dataset, not raw OCR. |

### Core principle (must not drift)

> Every material conclusion → reasoning/rule → normalized fact(s) → evidence → document → page → region → original bytes.

------------------------------------------------------------------------

## 2. Target System Shape

```text
                    PIE UI  /  Partner API
                              │
                         API Gateway
                    Auth · Tenant · Case ACL
                              │
               ┌──────────────┼──────────────┐
               │                             │
           Job API                      Query API
               │                             │
               ▼                             ▼
        Queue / Workers              Case Intelligence API
               │                     (chat, search, graphs,
               ▼                      review, reports)
        Document Pipeline
               │
    Integrity → OCR → Evidence → Extract → Normalize
               │
               ▼
        Cross-doc Reconciliation
               │
               ▼
     Case Knowledge Store (Postgres)
     + Vectors (pgvector) + Object store (S3)
               │
               ▼
     Graphs · Memory · Hybrid RAG · Reports
```

**Gemini usage pattern**

| Workload | Model class | Notes |
|----------|-------------|--------|
| Page quality, classification, layout | Flash | Cheap, high volume |
| OCR / multimodal extraction | Flash or Vision path | Dual-OCR on critical pages |
| Structured field extraction | Flash + JSON schema | Versioned prompts |
| Legal / conflict reasoning | Pro | High stakes only |
| Chat synthesis over retrieved evidence | Flash or Pro by risk | Always cite evidence IDs |
| Embeddings | Gemini embedding model | Tenant/case filtered |

Always sit Gemini behind a `LLMProvider` interface so providers stay swappable.

------------------------------------------------------------------------

## 3. Phase Roadmap Overview

| Phase | Name | Outcome | Rough exit criteria |
|-------|------|---------|---------------------|
| **0** | Foundation | Repo, tenancy, cases, storage, jobs | Upload a PDF to a case; job runs end-to-end with no AI |
| **1** | Evidence spine | Pages, OCR, evidence store | Every page has text + provenance; Gemini behind provider |
| **2** | Structured intelligence | Facts, entities, events | Extracted facts queryable with evidence links |
| **3** | Reconciliation & graphs | Conflicts, missing docs, graphs | Multi-doc case shows conflicts + document graph |
| **4** | Case memory & chat | Persistent intelligence + RAG chat | Chat cites evidence; corrections write back |
| **5** | Human review & compounding | Review queue, verified KB | Analyst decisions upgrade fact states |
| **6** | Domain due diligence depth | Ownership, legal, risk, GIS hooks | DD checklist + risk drivers from rules |
| **7** | Reports & partner API | TSR/DD report + multi-tenant API | Report from verified layer; partner can poll jobs |
| **8** | Scale, eval, cost control | Hardening | Golden docs, cost budgets, idempotent reprocess |

Implement **in order**. Later phases consume earlier truth layers. Do not jump to “smart chat” before evidence + facts exist.

------------------------------------------------------------------------

## Phase 0 — Foundation (platform skeleton)

**Goal:** A scalable multi-tenant shell with cases, uploads, durable jobs, and observability — zero LLM required.

### 0.1 Deliverables

- Monorepo / service layout aligned with architecture §76.
- Auth + tenant isolation.
- Case CRUD (`cases`, `case_members`, ACL).
- Document upload to object storage (immutable originals).
- Job table + queue worker (not FastAPI `BackgroundTasks` for heavy work).
- Health, request IDs, structured logs, basic metrics.

### 0.2 Data model (minimal)

```text
tenants
users / api_keys
cases
case_documents          # join: case_id, document_id, role (primary|supporting)
documents               # content_hash, mime, size, storage_uri, processing_version
processing_jobs
processing_stage_runs
audit_events
```

### 0.3 APIs

```text
POST   /v1/cases
GET    /v1/cases/{case_id}
POST   /v1/cases/{case_id}/documents          # initiate upload
POST   /v1/cases/{case_id}/documents/complete
GET    /v1/jobs/{job_id}
```

### 0.4 Engineering standards (lock in now)

- Idempotent stages keyed by `(document_hash, stage, stage_version, model/prompt version)`.
- Never overwrite original bytes.
- All queries scoped by `tenant_id` + `case_id`.
- Schema + prompt versioning tables from day one (even if empty).

### 0.5 Exit checklist

- [x] Upload PDF → stored in S3 → job `SUCCEEDED` with noop stages.
- [x] Second upload of same hash dedupes or links without re-storing bytes.
- [x] Cross-tenant document access fails closed.

------------------------------------------------------------------------

## Phase 1 — Evidence spine (Gemini enters)

**Goal:** Turn files into page-level evidence. This is the P0 architectural win.

### 1.1 Pipeline stages

```text
UPLOAD → HASH/DEDUP → INTEGRITY → PAGE SPLIT
  → PAGE QUALITY → CLASSIFY → OCR (Gemini) → EVIDENCE_ITEMS
```

### 1.2 Deliverables

- `document_pages`, `document_integrity_checks`, `page_extractions`, `evidence_items`.
- MIME sniffing, page count, blank/missing-page heuristics.
- OCR router: digital text extract first; Gemini Vision for scans/low quality.
- Dual OCR optional on critical pages (identifiers, registration blocks).
- `GeminiProvider` implementing classify / extract / embed.

### 1.3 Evidence object (required fields)

```json
{
  "evidence_id": "ev_...",
  "document_id": "doc_...",
  "case_id": "case_...",
  "page_number": 14,
  "text": "...",
  "normalized_text": "...",
  "bbox": [120, 430, 640, 480],
  "source_type": "OCR",
  "ocr_provider": "gemini",
  "ocr_confidence": 0.91,
  "extraction_version": "1.0",
  "model_run_id": "run_..."
}
```

### 1.4 Gemini practices

- Structured outputs / JSON schemas for classification.
- Record `prompt_id`, `prompt_version`, `model`, `temperature`, `schema_version` on every `model_runs` row.
- Cost tags: `tenant_id`, `case_id`, `stage`, `model`.

### 1.5 Exit checklist

- [x] Every page has extractable evidence rows.
- [x] Integrity warnings persisted (incomplete page sequence, low quality).
- [x] Re-running OCR with same versions is a no-op (idempotent).
- [x] Can open a page in UI and highlight evidence bbox/snippet.

------------------------------------------------------------------------

## Phase 2 — Structured intelligence

**Goal:** Gemini extracts **candidate facts**; code normalizes them into case intelligence — still single-document capable, multi-doc ready.

### 2.1 Agents (thin) + services (thick)

**Gemini agents (semantic):**

- `DocumentClassifier`
- `EntityExtractor`
- `ParcelExtractor` / identifier extractor
- `TransactionExtractor`
- `OwnershipEventExtractor`
- `EncumbranceExtractor`

**Deterministic services (code, not LLM):**

- Unit/area conversion, date parse/order, share math
- Identifier normalization (`183/2`, `KH-102`)
- Schema validation

### 2.2 Fact lifecycle

```text
Raw extraction → Candidate fact → Normalization → Validation
  → (later) Reconciliation → Verified / Conflict / Unknown
```

Verification states to support from the start:

```text
EXTRACTED | SUPPORTED | CORROBORATED | VERIFIED
CONFLICTING | AMBIGUOUS | UNVERIFIED
NOT_FOUND | NOT_PROVIDED | NOT_APPLICABLE | REQUIRES_REVIEW
```

### 2.3 Tables

```text
facts
fact_evidence
persons
person_aliases
person_identifiers
parcels
parcel_identifiers
ownership_events
ownership_event_parties
ownership_shares
document_classifications
model_runs
prompt_versions
```

### 2.4 Exit checklist

- [x] For a sale deed, PIE stores owner/buyer/seller/survey/area/date facts with evidence IDs.
- [x] UI: “Why this fact?” opens cited pages.
- [x] No fact exists without at least one evidence link (or explicit `NOT_FOUND` scoped to supplied docs).

------------------------------------------------------------------------

## Phase 3 — Cross-document reconciliation & graphs

**Goal:** Multi-document due diligence. Intelligence becomes a **case graph**, not a pile of extractions.

### 3.1 Reconciliation stage

Input: facts, entities, parcels, events, legal candidates.  
Output: supported facts, conflicts, ambiguities, missing evidence, review tasks.

Rules:

- Reconciliation **compares**; it does not invent.
- Prefer conservative entity matching (IDs > strong identifiers > names+relations > weak names).
- Persist conflicts as first-class rows.

### 3.2 First-class conflict & gap types

```text
OWNER_NAME_CONFLICT
SURVEY_NUMBER_CONFLICT
AREA_CONFLICT
DATE_CONFLICT
SHARE_CONFLICT
OWNERSHIP_SEQUENCE_CONFLICT
ENCUMBRANCE_STATUS_CONFLICT
OCR_CONFLICT
… (extensible registry)
```

Missing evidence examples:

- Prior deed referenced but not uploaded  
- Mortgage mentioned, release deed absent  
- EC / mutation referenced, not provided  

### 3.3 Graphs to persist

**A. Document relationship graph**

```text
Sale Deed 2020
  ├── references → Sale Deed 2005   (REFERENCED_BUT_MISSING | PROVIDED)
  ├── references → Mutation 2020
  └── references → EC 2020
```

**B. Entity / event graph**

```text
Person/Org nodes ← party roles → OwnershipEvent nodes → Parcel nodes
```

Store graph edges in Postgres (`graph_nodes`, `graph_edges`) or as relational projections first; avoid Redis as source of truth. Optional Neo4j later if query patterns demand it.

### 3.4 Completeness scorecard (per case)

```text
File integrity | Page completeness | OCR quality
Ownership evidence | Transaction chain | Encumbrance evidence | GIS
```

Statuses: `PASS | WARNING | PARTIAL | INCOMPLETE | PROVIDED | GOOD | …`

### 3.5 Exit checklist

- [x] Two conflicting deeds produce an `OPEN` conflict, not a silent “winner.”
- [x] Document graph shows missing referenced instruments.
- [x] Case overview shows completeness scorecard + open conflicts count.

------------------------------------------------------------------------

## Phase 4 — Case memory, hybrid RAG, and agentic chat

**Goal:** PIE behaves like a durable diligence agent: it **remembers the case**, retrieves correctly, and answers with an evidence contract.

### 4.1 One truth layer for chat and reports

```text
              Evidence / Facts / Conflicts
                     /            \
                    ▼              ▼
              Chat / RAG         Reports
```

Never let chat read only raw OCR while reports read facts.

### 4.2 Retrieval stack (RAG v2)

```text
query → classify → extract entities/ids
  → metadata filters (tenant, case, doc type, date)
  → hybrid retrieval
       ├── vector (chunks + fact embeddings)
       ├── keyword / BM25
       ├── exact identifier search
       └── entity / temporal / graph neighborhood
  → rank + conflict-aware packing
  → Gemini synthesis
  → answer + evidence + status
```

Exact ID search is mandatory for survey/gata/khata/registration numbers.

### 4.3 Answer contract (every factual reply)

```json
{
  "answer": "...",
  "status": "SUPPORTED",
  "evidence": [
    {
      "document_id": "...",
      "page": 14,
      "snippet": "...",
      "bbox": [],
      "evidence_id": "ev_..."
    }
  ],
  "conflicts": [],
  "missing_evidence": [],
  "open_questions": []
}
```

Statuses: `SUPPORTED | PARTIALLY_SUPPORTED | CONFLICTING | INSUFFICIENT_EVIDENCE | NOT_FOUND | REQUIRES_REVIEW`

### 4.4 Case memory (compounding intelligence)

Persist beyond chat logs:

```text
case_memories
  - key facts summary (versioned)
  - open questions
  - user preferences for the case
  - last reconciliation snapshot id

conversations
conversation_messages   # role, content, retrieval_trace_id, answer_status

memory_writebacks
  - from chat corrections / confirmations
  - links to fact_id / review_decision_id
```

**Memory write paths**

| Signal | Effect |
|--------|--------|
| User confirms a fact | State → `VERIFIED` (or `CORROBORATED`) |
| User rejects a fact | State → rejected; optional alternate |
| User uploads new doc | Reconcile delta; refresh memory summary |
| User answers “missing deed?” | Create `MissingEvidence` task or mark provided |
| Chat discovers new alias | Candidate alias pending review |

### 4.5 Chat UX requirements

- Case-scoped threads (not global free chat).
- Show citations inline; click → page viewer.
- Surfacing conflicts/missing docs without being asked.
- “What should I upload next?” from missing-evidence graph.
- Conversation may use Flash; escalate to Pro when status would be `REQUIRES_REVIEW` or legal interpretation.

### 4.6 Guardrails (system prompt + post-checks)

1. Never invent identifiers, dates, owners.  
2. Never resolve conflicts without evidence.  
3. Never convert not-found → does-not-exist.  
4. Cite page/evidence for material claims.  
5. Separate document statements from conclusions.  
6. Ask for additional documents when required.

Post-check: if answer asserts a critical ID not present in retrieved evidence → refuse / downgrade status.

### 4.7 Exit checklist

- [x] “Who is the current owner?” returns status + citations.
- [x] “What about survey 183/2?” hits exact ID path first.
- [x] After confirming a fact in UI, subsequent chat uses `VERIFIED` state.
- [x] New document upload updates retrieval without rebuilding unrelated cases.

------------------------------------------------------------------------

## Phase 5 — Human review & knowledge compounding

**Goal:** High-risk ambiguity becomes a product workflow; analyst work permanently improves the case KB.

### 5.1 Review router

Create `review_tasks` when:

- Critical ID OCR conflict  
- Owner identity ambiguity  
- Encumbrance unresolved  
- Share math inconsistency  
- High-impact legal finding  
- User flags an answer  

### 5.2 Review decision model

```text
review_tasks
review_decisions   # approve | reject | merge | split | request_docs | annotate
```

Decisions must:

- Update fact/entity/conflict state  
- Write audit trail  
- Invalidate/recompute dependent findings  
- Refresh case memory snapshot  

### 5.3 Compounding loop (agent-like improvement)

```text
More docs + human decisions
        ↓
Better entity resolution priors (case-local, then optional tenant-level)
        ↓
Fewer false merges / better retrieval filters
        ↓
Higher verified-fact ratio
        ↓
Better reports and chat
```

Start with **case-local** learning (aliases, preferred spellings, confirmed links). Tenant-level shared dictionaries only with explicit promotion and audit.

### 5.4 Exit checklist

- [x] Review queue filterable by severity / case / type.  
- [x] Decision changes appear in chat and report without re-OCR.  
- [x] Full audit: who changed what fact, when, why.

------------------------------------------------------------------------

## Phase 6 — Due diligence depth (domain engines)

**Goal:** Specialize PIE for title / property DD (and keep modules swappable for other DD packs later).

### 6.1 Modules (implement as domain packages)

| Module | Responsibility |
|--------|----------------|
| Temporal ownership engine | Events → ordered chain; detect gaps |
| Share accounting | Deterministic totals; flag drift |
| Survey / parcel identity | Normalize + transformation events |
| Area reconciliation | Unit convert + conflict |
| Legal findings | Layer 1 extract / Layer 2 reason (Gemini Pro) |
| Risk engine | Versioned weighted drivers (not one opaque score) |
| GIS hooks (optional) | Geometry validity ≠ legal identity |

### 6.2 Risk presentation

```text
Risk: HIGH
Drivers:
  - Unresolved mortgage (+30)
  - Conflicting owner name (+25)
  - Survey ambiguity (+20)
```

Weights configurable & versioned. Never present score as legal truth.

### 6.3 Critical-fact confidence (multi-dimension)

```text
Extraction confidence
Evidence quality
Cross-document agreement
Temporal consistency
External verification (if any)
Human review status
```

### 6.4 Exit checklist

- [x] Ownership chain timeline UI from events.  
- [x] Mortgage-without-release example yields `UNRESOLVED` + missing release, not “clear.”  
- [x] Risk panel lists drivers tied to findings/conflicts.

------------------------------------------------------------------------

## Phase 7 — Reports & multi-tenant product surface

**Goal:** Generate diligence outputs from the **verified dataset**; expose partner-ready APIs.

### 7.1 Report generation

Sources: reconciled facts, conflicts, missing evidence, review states, risk drivers.

Sections (typical TSR / DD memo):

1. Scope of documents reviewed  
2. Parcel / property identity  
3. Parties & ownership chain  
4. Encumbrances & charges  
5. Conflicts & unresolved items  
6. Missing evidence checklist  
7. Risk drivers  
8. Recommended next verifications  

Language rules: evidence-first; no false certainty; distinguish claims vs verified conclusions.

### 7.2 APIs (productized)

```text
POST /v1/cases/{id}/analyze          # enqueue full or incremental
GET  /v1/cases/{id}/intelligence     # facts, graphs, scorecard
POST /v1/cases/{id}/chat
GET  /v1/cases/{id}/conflicts
GET  /v1/cases/{id}/missing-evidence
POST /v1/cases/{id}/review/{task}/decide
GET  /v1/cases/{id}/report           # PDF/JSON
```

### 7.3 Multi-tenancy hard requirements

- No cross-tenant vector retrieval.  
- S3 paths prefixed by tenant.  
- API keys scoped; audit every export.  
- PII-aware logging redaction.

### 7.4 Exit checklist

- [ ] Report regeneration after a review decision changes only dependent sections.  
- [ ] Partner can complete upload → job → report without UI.  
- [ ] Chat and report never disagree on conflict status for the same fact set.

------------------------------------------------------------------------

## Phase 8 — Scale, evaluation, cost, and ops

**Goal:** Production resilience: cheap where possible, strict where it matters.

### 8.1 Cost routing

| Use | Engine |
|-----|--------|
| Hash, integrity, units, dates, geometry, conflict rules | Code |
| Classify, OCR, bulk extract | Gemini Flash |
| Legal ambiguity, hard reconciliation narrative | Gemini Pro |
| Material unresolved risk | Human review |

### 8.2 Incremental reprocessing

- New document → process delta → reconcile only affected entities/events.  
- Prompt/schema bump → selective re-extract by stage version.  
- Keep prior evidence; version facts.

### 8.3 Evaluation (gate releases)

- Deterministic unit tests (shares, dates, IDs).  
- Golden document packs with expected facts/conflicts.  
- Adversarial: missing pages, similar names, dual survey numbers.  
- RAG metrics: citation precision, hallucination rate, ID retrieval hit rate.  
- Quality gates: block release if citation or conflict-detection regresses.

### 8.4 Observability

Trace per `case_id` / `document_id` / `job_id` / `stage` / `model_run_id`.  
Dashboards: cost per case, stage latency, OCR confidence distributions, review backlog, verified-fact ratio.

### 8.5 Failure containment

- Stage timeouts + retries with idempotency.  
- Partial success: evidence kept even if later stage fails.  
- AI failure → `REQUIRES_REVIEW` / `INSUFFICIENT_EVIDENCE`, never silent fill-in.

### 8.6 Exit checklist

- [ ] Cost budget alerts per tenant.  
- [ ] Golden pack CI.  
- [ ] Reprocess docs after prompt vN without full corpus downtime.

------------------------------------------------------------------------

## 4. Recommended Project Structure

```text
pie/
├── apps/
│   ├── api/                 # FastAPI (or equiv) — Job + Query APIs
│   ├── worker/              # Queue consumers / pipeline stages
│   └── web/                 # Case workspace UI
│
├── packages/
│   ├── domain/
│   │   ├── cases/
│   │   ├── documents/
│   │   ├── evidence/
│   │   ├── entities/
│   │   ├── ownership/
│   │   ├── legal/
│   │   ├── findings/
│   │   ├── graphs/
│   │   ├── memory/
│   │   └── review/
│   ├── pipeline/
│   │   ├── ingestion/
│   │   ├── extraction/
│   │   ├── normalization/
│   │   ├── reconciliation/
│   │   ├── validation/
│   │   └── synthesis/
│   ├── ai/
│   │   ├── providers/       # GeminiProvider, MockProvider
│   │   ├── prompts/         # versioned
│   │   ├── agents/
│   │   └── schemas/
│   ├── retrieval/           # hybrid search, ID search, guardrails
│   ├── reports/
│   └── observability/
│
├── infrastructure/          # postgres, s3, redis, queue terraform/compose
└── tests/
    ├── unit/
    ├── golden_docs/
    └── adversarial/
```

------------------------------------------------------------------------

## 5. Minimal Schema Map (evolution by phase)

| Phase | Add |
|-------|-----|
| 0 | tenants, users, cases, documents, jobs, audit |
| 1 | pages, integrity, evidence, model_runs |
| 2 | facts, entities, parcels, ownership_events |
| 3 | conflicts, missing_evidence, graph_nodes/edges |
| 4 | embeddings, conversations, case_memories, writebacks |
| 5 | review_tasks, review_decisions |
| 6 | legal_findings, geo_findings, risk_snapshots |
| 7 | report_artifacts, api_keys expansions |
| 8 | eval_runs, cost_budgets (optional) |

------------------------------------------------------------------------

## 6. Build Order for a Thin Vertical Slice (first useful product)

If you need a demoable path before full Phase 6–7:

1. **Phase 0** shell  
2. **Phase 1** evidence on 1–3 sample title docs  
3. **Phase 2** extract owners + survey + dates  
4. **Phase 3** two-doc conflict + missing deed  
5. **Phase 4** case chat with citations + memory writeback on confirm  
6. Then deepen domain (Phase 6) and polish report/API (Phase 7)

This slice already demonstrates “upload many docs → build intelligence → chat like an agent that remembers.”

------------------------------------------------------------------------

## 7. Implementation Principles (do not violate)

1. **Evidence before eloquence** — no uncited material claims.  
2. **LLM proposes; store decides** — facts have states, not vibes.  
3. **Conflicts and gaps are data** — not prompt afterthoughts.  
4. **One truth layer** — chat, UI, report, API share it.  
5. **Deterministic where possible** — math, dates, geometry, ID match.  
6. **Idempotent stages** — safe retries and version bumps.  
7. **Case-scoped memory** — intelligence compounds per matter.  
8. **Human review is architecture** — not a support ticket.  
9. **Provider abstraction** — Gemini default, not a hardcoded sprawl.  
10. **Cost-aware routing** — Flash by default; Pro and humans by risk.

------------------------------------------------------------------------

## 8. Definition of Done for PIE v1

PIE v1 is done when a diligence analyst can:

1. Create a case and upload a document pack over multiple sessions.  
2. See progressive intelligence (facts, graph, conflicts, missing docs) improve with each upload.  
3. Ask questions in chat and receive **status + citations**, never fabricated certainty.  
4. Confirm/correct findings and see the knowledge base update.  
5. Export a report that matches the same truth layer as chat.  
6. Leave and return later — case memory and graphs persist.

That is the product bar: **a compounding, evidence-grounded due diligence agent**, not a disposable PDF chatbot.

------------------------------------------------------------------------

## 9. Traceability to Architecture V2

| PIE concern | Architecture V2 anchors |
|-------------|-------------------------|
| Evidence-first | §§4, 11, 47, 80–82 |
| Pipeline stages | §§7–10, 38–41 |
| Facts & states | §§12–13 |
| Entities & ownership events | §§14–18 |
| Graphs & completeness | §§50–51 |
| RAG + answer contract | §§28–31, 69 |
| Agents vs code | §§65–67 |
| Review | §§34–35 |
| Risk | §§32–33 |
| Data model / structure | §§46, 76 |
| Migration-style phasing | §§77, 79 |
| Gemini abstraction & cost | §§42–44 |

Use `AUXILIUM_AI_ARCHITECTURE_V2.md` as the deep design reference; use **this guide** as the build sequence for the PIE product.

------------------------------------------------------------------------

## 10. Suggested Near-Term Backlog (first 4 sprints)

**Sprint 1:** Phase 0 + document upload/jobs  
**Sprint 2:** Phase 1 evidence + Gemini OCR provider  
**Sprint 3:** Phase 2 fact extraction for core fields + “why?” UI  
**Sprint 4:** Phase 3 reconciliation MVP + Phase 4 chat with citations  

After Sprint 4, run a real internal diligence pack and measure: citation precision, conflict catch rate, and time-to-first-useful-answer. Let those metrics drive Phase 5–8 prioritization.
