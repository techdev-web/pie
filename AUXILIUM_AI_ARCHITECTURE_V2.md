# Auxilium AI Title Intelligence Engine --- Improved Architecture & Technical Design

**Document:** `AUXILIUM_AI_ARCHITECTURE_V2.md`\
**Purpose:** Production-oriented architecture for a more accurate,
explainable, resilient and maintainable Indian land/title intelligence
system.

------------------------------------------------------------------------

## 1. Executive Summary

Auxilium is an AI-powered document intelligence platform for Indian land
and property records. The current system already provides:

-   Multimodal OCR and document extraction
-   Specialized AI agents for NER, legal analysis and ownership
-   RAG over uploaded documents
-   Ownership lineage graphs
-   GIS validation
-   TSR generation
-   Multi-tenant partner APIs

The next version should not primarily be a larger or more capable LLM
pipeline. The main architectural improvement should be a shift from:

> **"Extract → ask agents → synthesize a report"**

to:

> **"Acquire → inspect → extract with evidence → normalize →
> cross-document reconcile → validate → reason with explicit uncertainty
> → generate report."**

The central principle is:

> **AI may propose facts and interpretations, but every material
> conclusion must be traceable to evidence, independently validated
> where possible, and explicitly marked when it cannot be established.**

The system should therefore treat these as first-class concepts:

1.  **Evidence**
2.  **Uncertainty**
3.  **Document completeness**
4.  **Cross-document conflicts**
5.  **Entity identity**
6.  **Temporal ownership events**
7.  **Parcel identity**
8.  **Legal claims vs legal evidence**
9.  **Verification state**
10. **Human review**

This architecture is designed to reduce false confidence rather than
merely improve average LLM accuracy.

------------------------------------------------------------------------

# 2. Goals

## 2.1 Primary goals

The improved system must:

-   Extract reliable structured facts from Indian land documents.
-   Preserve exact evidence for every important fact.
-   Detect OCR uncertainty instead of silently guessing.
-   Detect missing pages and suspicious document incompleteness.
-   Distinguish "not found" from "not present".
-   Detect contradictions across documents.
-   Resolve owners and aliases without aggressively merging different
    people.
-   Model ownership as dated events rather than only a graph of
    relationships.
-   Track survey/gata/khata number transformations over time.
-   Validate parcel geometry separately from textual parcel identity.
-   Distinguish document statements from verified legal conclusions.
-   Generate TSRs that clearly communicate evidence, conflicts and
    unresolved issues.
-   Support human review of high-risk findings.
-   Make every result reproducible and auditable.
-   Control Gemini/OCR cost through intelligent routing.
-   Support multi-tenant API access without cross-tenant retrieval.

## 2.2 Secondary goals

-   Easy replacement of individual AI providers.
-   Provider-independent internal data models.
-   Deterministic validation wherever deterministic logic is possible.
-   Testability without calling an LLM for every test.
-   Incremental processing and reprocessing.
-   Idempotent document ingestion.
-   Strong observability and cost accounting.
-   Versioned prompts, models and extraction schemas.

------------------------------------------------------------------------

# 3. Non-Goals

The system must not claim to:

-   Establish legal title solely from an LLM response.
-   Guarantee that an unmentioned encumbrance does not exist.
-   Treat OCR output as authoritative source data.
-   Treat a valid polygon as proof of legal parcel identity.
-   Infer missing evidence as a positive fact.
-   Replace a qualified legal professional where legal verification is
    required.
-   Resolve ambiguous ownership merely because an LLM can produce a
    plausible answer.

The system is a **document intelligence and verification-assistance
platform**, not an autonomous legal authority.

------------------------------------------------------------------------

# 4. Core Architectural Principle

## 4.1 Evidence-first architecture

Every material fact should have an evidence chain:

``` text
Conclusion
    ↓
Reasoning / rule
    ↓
Normalized fact(s)
    ↓
Evidence references
    ↓
Document
    ↓
Page
    ↓
Bounding region / text span
    ↓
Original image / PDF
```

Example:

``` text
Conclusion:
"Mortgage status requires verification"

Evidence:
    Sale Deed A, page 14
    Khatauni B, page 7
    Release Deed C not provided

Status:
    CONFLICTING / INCOMPLETE

Confidence:
    HIGH that the mortgage is mentioned
    LOW that it remains active

Action:
    Obtain and verify Release Deed C
```

This is substantially safer than returning:

``` json
{
  "mortgage": true,
  "risk_score": 75
}
```

without evidence.

------------------------------------------------------------------------

# 5. Target Architecture

``` text
                         CLIENTS
             ┌──────────────┼──────────────┐
             │              │              │
          Auxilium UI   Partner APIs   Internal Tools
             │              │              │
             └──────────────┼──────────────┘
                            │
                       API Gateway
                            │
                    Authentication / Tenant
                            │
                 ┌──────────┴──────────┐
                 │                     │
             Job API              Query API
                 │                     │
                 ▼                     ▼
          Workflow / Queue       RAG Query Engine
                 │                     │
                 ▼                     │
        Document Ingestion             │
                 │                     │
        ┌────────┼────────┐            │
        ▼        ▼        ▼            │
    Integrity  OCR      Classification │
      Check   Router      / Layout      │
        │        │        │             │
        └────────┼────────┘             │
                 ▼                      │
          Evidence Store                │
                 │                      │
                 ▼                      │
       Structured Extraction            │
                 │                      │
       ┌─────────┼─────────┐            │
       ▼         ▼         ▼            │
    Entities   Events    Parcels        │
       │         │         │            │
       └─────────┼─────────┘            │
                 ▼                      │
       Cross-document Reconciliation    │
                 │                      │
       ┌─────────┼────────────┐         │
       ▼         ▼            ▼         │
    Temporal   Legal        Geo         │
    Validator Validator   Validator     │
       │         │            │         │
       └─────────┼────────────┘         │
                 ▼                      │
          Risk / Findings Engine        │
                 │                      │
                 ▼                      │
        Human Review Router             │
                 │                      │
                 ▼                      │
          TSR / JSON / CSV              │
                                        │
                 ┌──────────────────────┘
                 ▼
          Evidence-grounded RAG
                 │
                 ▼
          Answer + citations
```

------------------------------------------------------------------------

# 6. Recommended Service Boundaries

The system should be organized into logical services/modules even if
initially deployed as one FastAPI application.

## 6.1 API service

Responsibilities:

-   Authentication
-   Tenant authorization
-   Upload initiation
-   Job creation
-   Status
-   Results
-   Search
-   Q&A
-   Report generation

Should not contain heavy AI processing directly inside request handlers.

------------------------------------------------------------------------

## 6.2 Workflow / processing service

Responsibilities:

-   Orchestrate asynchronous processing
-   Retry failed steps
-   Resume checkpoints
-   Control concurrency
-   Manage dependencies between stages
-   Persist processing state

Recommended pattern:

``` text
API
 ↓
Job record
 ↓
Queue
 ↓
Worker
 ↓
Stage 1
 ↓
Stage 2
 ↓
Stage 3
```

Avoid relying primarily on FastAPI `BackgroundTasks` for long-running
document processing.

------------------------------------------------------------------------

# 7. Document Ingestion

## 7.1 Upload

On upload:

1.  Generate immutable `document_id`.
2.  Compute SHA-256 content hash.
3.  Detect MIME type from bytes.
4.  Store original file unchanged.
5.  Record size, page count and metadata.
6.  Create processing job.
7.  Never overwrite original bytes.

Example:

``` json
{
  "document_id": "doc_...",
  "content_hash": "...",
  "mime_type": "application/pdf",
  "source_filename": "sale_deed.pdf",
  "tenant_id": "...",
  "processing_version": "2.0"
}
```

------------------------------------------------------------------------

# 8. Document Integrity Layer

Before OCR or AI processing, run deterministic checks.

## 8.1 Checks

-   PDF corruption
-   Page count
-   Duplicate pages
-   Blank pages
-   Near-duplicate pages
-   Page numbering continuity
-   Rotation
-   Resolution
-   Image quality
-   Encryption
-   Embedded text availability
-   Missing/invalid PDF objects
-   Suspiciously low page count
-   Document metadata anomalies

## 8.2 Page completeness

If pages contain printed numbers:

``` text
1 / 24
2 / 24
3 / 24
...
7 / 24
9 / 24
```

the system should raise:

``` text
MISSING_PAGE_SUSPECTED: page 8
```

This does not prove a page is missing, but it prevents the pipeline from
treating the document as complete.

------------------------------------------------------------------------

# 9. Page Quality Classification

Each page receives a deterministic/ML quality profile.

``` json
{
  "page": 7,
  "text_density": 0.72,
  "image_quality": 0.41,
  "rotation": 90,
  "script": "DEVANAGARI",
  "handwriting_probability": 0.78,
  "stamp_overlap_probability": 0.32,
  "quality_class": "LOW_CONFIDENCE_SCAN"
}
```

Recommended classes:

-   `DIGITAL_TEXT_HIGH`
-   `DIGITAL_TEXT_LOW`
-   `SCANNED_HIGH`
-   `SCANNED_LOW`
-   `HANDWRITTEN`
-   `HYBRID`
-   `TABLE_HEAVY`
-   `MAP`
-   `STAMP_HEAVY`
-   `SIGNATURE_HEAVY`
-   `GARBLED`
-   `BLANK`

------------------------------------------------------------------------

# 10. OCR Strategy

Do not use one OCR method for every page.

## 10.1 Routing

``` text
Digital text
    ↓
PyMuPDF extraction
    ↓
Validation
    ↓
Good? ── yes → use text
    │
    no
    ↓
Vision/OCR

Scanned page
    ↓
Local OCR
    ↓
Quality evaluation
    ↓
Good? ── yes → continue
    │
    no
    ↓
Gemini Vision

Handwritten / difficult
    ↓
Gemini Vision + optional specialist OCR
    ↓
Human review if critical fields remain uncertain
```

## 10.2 Dual OCR for critical pages

For pages containing:

-   Survey number
-   Gata number
-   Khata number
-   Owner name
-   Area
-   Consideration amount
-   Registration number
-   Mortgage details
-   Date

the system should optionally perform two independent extraction passes.

Example:

``` text
OCR A → Gata 183/2
OCR B → Gata 183/7
```

This should produce:

``` text
CONFLICTING_OCR
```

rather than selecting one automatically.

------------------------------------------------------------------------

# 11. Evidence Store

This is a major architectural addition.

Create an explicit evidence model.

## 11.1 Evidence object

``` json
{
  "evidence_id": "ev_123",
  "document_id": "doc_123",
  "page_number": 14,
  "text": "Gata No. 183/2",
  "normalized_text": "gata 183/2",
  "bbox": [120, 430, 640, 480],
  "source_type": "OCR",
  "ocr_provider": "gemini",
  "ocr_confidence": 0.91,
  "image_hash": "...",
  "extraction_version": "2.1"
}
```

For digital PDFs, store character/word spans where available.

For images, store bounding boxes.

For every important field, preserve:

``` text
source document
→ page
→ region
→ extracted text
→ extraction method
→ confidence
```

------------------------------------------------------------------------

# 12. Structured Facts

AI output should never directly become the final database truth.

Instead:

``` text
Raw extraction
      ↓
Candidate fact
      ↓
Normalization
      ↓
Validation
      ↓
Reconciliation
      ↓
Verified fact / Conflict / Unknown
```

Example:

``` json
{
  "fact_type": "SURVEY_NUMBER",
  "value": "183/2",
  "status": "CONFLICTING",
  "confidence": 0.87,
  "evidence_ids": [
    "ev_123",
    "ev_991"
  ]
}
```

------------------------------------------------------------------------

# 13. Verification States

Every important fact should have a state.

Recommended states:

``` text
EXTRACTED
SUPPORTED
CORROBORATED
VERIFIED
CONFLICTING
AMBIGUOUS
UNVERIFIED
NOT_FOUND
NOT_PROVIDED
NOT_APPLICABLE
REQUIRES_REVIEW
```

These states are more useful than a single confidence score.

## Important distinction

Do not say:

> "No mortgage exists."

when the system only knows:

> "No mortgage was found in the supplied documents."

Use:

``` text
mortgage_status = NOT_FOUND
evidence_scope = SUPPLIED_DOCUMENTS
```

------------------------------------------------------------------------

# 14. Entity Resolution

Names and identities require conservative matching.

## 14.1 Entity matching hierarchy

Use deterministic evidence first:

1.  Government identifier
2.  Registration identifier
3.  Father/mother/spouse relationship
4.  Address
5.  Village
6.  Age/date
7.  Document role
8.  Name similarity
9.  Transliteration similarity

Do not merge solely on:

``` text
name_similarity > threshold
```

Instead:

``` text
strong identifiers → automatic merge

weak identifiers + conflicting attributes
→ separate entities

ambiguous
→ REVIEW
```

------------------------------------------------------------------------

# 15. Person Identity Model

Example:

``` text
Person
 ├── canonical_name
 ├── aliases
 ├── identifiers
 ├── parent relationships
 ├── spouse relationships
 ├── addresses
 ├── evidence
 └── identity_confidence
```

Every alias must retain its evidence.

Example:

``` text
"राम कुमार"
"Ram Kumar"
"Ramkumar"
```

can be associated with one person only when supporting evidence exists.

------------------------------------------------------------------------

# 16. Ownership Should Be Event-Based

Do not derive ownership solely from a graph.

Use an immutable chronological event model.

``` text
OwnershipEvent
---------------------------
event_id
date
event_type
source_document
transferor
transferee
parcel
share
consideration
registration_no
evidence
status
```

Event types:

-   PURCHASE
-   SALE
-   GIFT
-   INHERITANCE
-   PARTITION
-   RELEASE
-   MORTGAGE
-   LEASE
-   COURT_ORDER
-   MUTATION
-   CORRECTION
-   UNKNOWN_TRANSFER

Then build the ownership graph from these events.

``` text
Documents
    ↓
Ownership Events
    ↓
Temporal Validation
    ↓
Ownership Graph
```

This makes the graph explainable.

------------------------------------------------------------------------

# 17. Temporal Ownership Engine

The engine should validate chronology.

Example:

``` text
A → B on 2001
B → C on 1998
```

This should produce:

``` text
TEMPORAL_CONFLICT
```

Similarly:

``` text
A owns 100%
A sells 60% to B
A later sells 50% to C
```

should produce:

``` text
SHARE_OVERSELL_CONFLICT
```

The system should not merely build a visually plausible graph.

------------------------------------------------------------------------

# 18. Share Accounting

Track ownership mathematically.

Example:

``` text
Initial:
A = 1.00

Sale:
A → B = 0.40

Remaining:
A = 0.60
B = 0.40
```

Then:

``` text
A → C = 0.70
```

produces:

``` text
INVALID_SHARE_TRANSFER
```

unless additional evidence explains the discrepancy.

This type of deterministic rule is preferable to asking an LLM to reason
about percentages.

------------------------------------------------------------------------

# 19. Survey / Parcel Identity Engine

Parcel identity should be separated from ownership.

Model:

``` text
ParcelIdentity
    ↓
Historical identifiers
    ↓
Current identifier
    ↓
Geometry
    ↓
Documents
```

Track:

-   State
-   District
-   Tehsil/Subdistrict
-   Village
-   Survey number
-   Gata number
-   Khasra number
-   Khata number
-   Subdivision
-   Old number
-   New number
-   Area
-   Geometry
-   Source/evidence

------------------------------------------------------------------------

# 20. Survey Number Transformation

Represent changes explicitly:

``` text
183
 │
 ├── 183/1
 ├── 183/2
 └── 183/3
```

or:

``` text
Old Survey 183
       ↓
Re-survey
       ↓
Gata 521
```

Every transformation requires evidence.

If the system cannot establish the mapping:

``` text
mapping_status = AMBIGUOUS
```

not:

``` text
mapping_status = VERIFIED
```

------------------------------------------------------------------------

# 21. Area Reconciliation

Area should be normalized into canonical units.

Store:

``` text
raw_value
raw_unit
normalized_sq_m
normalized_acre
normalized_hectare
```

Then compare:

``` text
Sale deed = 2.50 acre
Khatauni = 2.20 acre
GIS = 2.31 acre
```

Generate:

``` text
AREA_CONFLICT
```

with tolerances appropriate to the source and unit conversion.

Do not let the LLM decide whether a 10% discrepancy is acceptable.

------------------------------------------------------------------------

# 22. GIS Architecture

Separate three concepts:

### A. Geometry validity

Is the polygon mathematically valid?

### B. Geometry identity

Does this polygon correspond to the claimed survey/parcel?

### C. Document boundary consistency

Does the written boundary description agree with the geometry?

These are different validations.

``` text
              GIS Validator
                   │
       ┌───────────┼───────────┐
       ▼           ▼           ▼
 Geometry       Identity     Boundary
 validity       matching     consistency
```

------------------------------------------------------------------------

# 23. GIS Evidence

For every geometry relationship store:

``` json
{
  "parcel_id": "parcel_123",
  "geometry_source": "KML",
  "claimed_survey_no": "183/2",
  "overlay_source": "BhuNaksha",
  "overlap_ratio": 0.94,
  "centroid_distance_m": 4.8,
  "identity_status": "SUPPORTED"
}
```

A geometry match should never automatically become:

``` text
LEGAL_TITLE_VERIFIED
```

------------------------------------------------------------------------

# 24. Document Classification

Document classification should produce:

``` json
{
  "document_type": "SALE_DEED",
  "state": "UP",
  "language": ["Hindi", "English"],
  "confidence": 0.94,
  "evidence_ids": ["ev_1", "ev_2"]
}
```

If classification is uncertain:

``` text
SALE_DEED: 0.48
PARTITION_DEED: 0.43
OTHER: 0.09
```

route for review or broader extraction.

Do not force one category.

------------------------------------------------------------------------

# 25. Legal Analysis Architecture

The LegalAgent should be split into two layers.

## Layer 1 --- Legal fact extraction

Extract only what the document says.

Examples:

``` text
Mortgage mentioned: YES
Mortgagee: Bank X
Date: 2014
Document No: 1234
Release mentioned: NO
```

## Layer 2 --- Legal reasoning

Use deterministic rules + legal knowledge + LLM reasoning.

Example:

``` text
Mortgage mentioned
+
No release document supplied
+
Current title document claims clear title
=
UNRESOLVED_ENCUMBRANCE
```

The system should not jump directly from:

``` text
"mortgage"
```

to:

``` text
"property is encumbered"
```

------------------------------------------------------------------------

# 26. Legal Finding Model

Every finding should contain:

``` json
{
  "finding_id": "f_123",
  "category": "ENCUMBRANCE",
  "severity": "HIGH",
  "statement": "A mortgage is referenced in the supplied records.",
  "status": "UNRESOLVED",
  "evidence_ids": ["ev_11", "ev_29"],
  "missing_evidence": [
    "release deed",
    "latest encumbrance certificate"
  ],
  "recommended_action": "Verify current mortgage discharge status."
}
```

This makes the report actionable rather than simply assigning a score.

------------------------------------------------------------------------

# 27. External Verification Layer

The system should distinguish:

``` text
DOCUMENT-DERIVED
```

from:

``` text
EXTERNALLY-VERIFIED
```

Potential external sources can include, where legally and technically
permitted:

-   Registration systems
-   Revenue portals
-   Court records
-   Encumbrance records
-   Cadastral/GIS systems
-   Government datasets

External data should never silently overwrite document evidence.

Instead:

``` text
Document says X
Government source says Y
→ CROSS_SOURCE_CONFLICT
```

------------------------------------------------------------------------

# 28. RAG v2

RAG should retrieve evidence, not just chunks.

Instead of:

``` text
query → chunks → LLM
```

use:

``` text
query
 ↓
query classification
 ↓
entity extraction
 ↓
metadata filtering
 ↓
hybrid retrieval
 ├── vector
 ├── keyword
 ├── exact identifier
 └── temporal/entity retrieval
 ↓
evidence ranking
 ↓
conflict detection
 ↓
LLM synthesis
 ↓
answer + evidence
```

------------------------------------------------------------------------

# 29. Exact Identifier Search

Land documents contain identifiers where vector similarity is often
inappropriate.

Examples:

``` text
183/2
521
KH-102
REG-1234
2001/458
```

The retrieval system should use:

-   Exact match
-   Normalized match
-   Token match
-   Fuzzy match
-   Vector search

in combination.

A query for:

> "What happened to survey 183/2?"

should first retrieve exact references to `183/2`, not rely primarily on
semantic similarity.

------------------------------------------------------------------------

# 30. RAG Answer Contract

Every factual answer should contain:

``` json
{
  "answer": "...",
  "status": "SUPPORTED",
  "evidence": [
    {
      "document_id": "...",
      "page": 14,
      "snippet": "...",
      "bbox": [...]
    }
  ],
  "conflicts": [],
  "missing_evidence": []
}
```

Possible answer statuses:

``` text
SUPPORTED
PARTIALLY_SUPPORTED
CONFLICTING
INSUFFICIENT_EVIDENCE
NOT_FOUND
REQUIRES_REVIEW
```

------------------------------------------------------------------------

# 31. RAG Guardrails

The model should be instructed:

1.  Never invent identifiers.
2.  Never invent dates.
3.  Never invent owners.
4.  Never resolve conflicts without evidence.
5.  Never convert "not found" into "does not exist".
6.  Cite the exact source page for material claims.
7.  Explicitly mention conflicting records.
8.  Say when evidence is incomplete.
9.  Separate document statements from conclusions.
10. Ask for additional documents when required.

------------------------------------------------------------------------

# 32. Risk Engine

Avoid a single opaque LLM-generated risk score.

Use a rule-based finding engine plus optional AI interpretation.

Example:

``` text
Missing deed chain              +30
Unresolved mortgage             +30
Owner identity conflict        +25
Area conflict                  +15
Survey mapping ambiguity       +20
Missing EC                       +20
OCR uncertainty on critical ID  +20
Geometry mismatch              +25
```

The exact weights should be configurable and versioned rather than
embedded in prompts.

The report should expose the reasons:

``` text
Risk:
HIGH

Drivers:
- Unresolved mortgage
- Conflicting owner name
- Survey number ambiguity
```

The system should not present the number as an objective legal truth.

------------------------------------------------------------------------

# 33. Critical-Fact Confidence

Use multiple dimensions instead of one confidence value.

``` text
Extraction confidence
Evidence quality
Cross-document agreement
Identity confidence
Temporal consistency
Verification status
```

Example:

``` text
Survey No:
Extraction confidence: 0.96
Cross-document agreement: LOW
Verification: CONFLICTING
```

This is more informative than:

``` text
confidence = 0.81
```

------------------------------------------------------------------------

# 34. Human Review System

Human review should be triggered automatically for high-impact
ambiguity.

Review triggers:

-   Conflicting survey numbers
-   Conflicting owners
-   Critical OCR disagreement
-   Missing pages
-   Ownership chronology conflict
-   Share arithmetic conflict
-   Geometry mismatch
-   Important legal ambiguity
-   Low-confidence identity match
-   Missing critical supporting document

Reviewer UI should show:

``` text
Original page image
        +
OCR text
        +
Extracted fact
        +
Alternative extraction
        +
Supporting documents
        +
Conflict explanation
        +
Approve / Reject / Correct
```

Human corrections should become structured feedback, not free-form notes
only.

------------------------------------------------------------------------

# 35. Human Review Is Part of the Architecture

Do not treat human review as an exception outside the system.

Model:

``` text
AI extraction
    ↓
Validation
    ↓
Risk assessment
    ↓
REVIEW_REQUIRED
    ↓
Human decision
    ↓
Verified result
```

Record:

-   reviewer
-   timestamp
-   old value
-   new value
-   reason
-   evidence
-   model version
-   prompt version

------------------------------------------------------------------------

# 36. Report Generation

The TSR should be generated from the structured evidence model, not
directly from raw LLM prose.

``` text
Evidence DB
    ↓
Validated facts
    ↓
Findings
    ↓
Ownership timeline
    ↓
Parcel/GIS results
    ↓
Report renderer
```

Report sections:

1.  Executive summary
2.  Property identity
3.  Document inventory
4.  Document completeness
5.  Ownership chronology
6.  Current ownership evidence
7.  Encumbrances
8.  Litigation references
9.  Parcel/GIS validation
10. Conflicts
11. Missing evidence
12. Risk findings
13. Verification checklist
14. Source/evidence appendix

------------------------------------------------------------------------

# 37. TSR Language Rules

Avoid absolute statements unless the evidence supports them.

Bad:

> "The property has no encumbrances."

Better:

> "No encumbrance was identified in the supplied documents reviewed by
> the system. Current encumbrance status should be verified against the
> latest applicable records."

Bad:

> "A is the legal owner."

Better:

> "The supplied records identify A as the owner/transfer recipient in
> the referenced transaction."

Bad:

> "Survey 183/2 is verified."

Better:

> "Survey 183/2 is consistently identified across Documents A and B and
> is supported by the supplied GIS overlay."

------------------------------------------------------------------------

# 38. Processing Workflow

Recommended workflow:

``` text
UPLOAD
  ↓
HASH / DEDUP
  ↓
INTEGRITY CHECK
  ↓
PAGE ANALYSIS
  ↓
DOCUMENT CLASSIFICATION
  ↓
OCR / TEXT EXTRACTION
  ↓
EVIDENCE CREATION
  ↓
STRUCTURED FACT EXTRACTION
  ↓
ENTITY NORMALIZATION
  ↓
PARCEL NORMALIZATION
  ↓
OWNERSHIP EVENTS
  ↓
CROSS-DOCUMENT RECONCILIATION
  ↓
TEMPORAL VALIDATION
  ↓
LEGAL FINDINGS
  ↓
GIS VALIDATION
  ↓
RISK ENGINE
  ↓
HUMAN REVIEW IF NEEDED
  ↓
FINAL DATASET
  ↓
RAG INDEX
  ↓
REPORT GENERATION
```

------------------------------------------------------------------------

# 39. Parallelism

Not everything should run sequentially.

After evidence extraction:

``` text
                 Evidence
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
      Entity      Parcel      Document
    extraction   extraction   facts
        │           │           │
        └───────────┼───────────┘
                    ▼
             Reconciliation
                    │
       ┌────────────┼────────────┐
       ▼            ▼            ▼
   Ownership       Legal        GIS
       │            │            │
       └────────────┼────────────┘
                    ▼
                Findings
```

This preserves concurrency while ensuring dependent stages wait for
their required evidence.

------------------------------------------------------------------------

# 40. Idempotency

Every processing stage should be idempotent.

Use:

``` text
document_hash
processing_version
stage_name
stage_version
model_version
prompt_version
```

as part of cache keys.

Example:

``` text
SHA256(document)
+
extraction_schema=v3
+
gemini_model=x
+
prompt_version=12
```

If unchanged, reuse results.

If the extraction prompt changes, re-run only affected stages.

------------------------------------------------------------------------

# 41. Incremental Reprocessing

Do not reprocess the entire document when only one component changes.

Example:

``` text
OCR unchanged
Entity extraction unchanged
Legal prompt changed
    ↓
Re-run LegalAgent
    ↓
Recompute findings
    ↓
Regenerate report
```

Similarly:

``` text
GIS overlay updated
    ↓
Re-run GIS validation
    ↓
Do not re-run OCR
```

------------------------------------------------------------------------

# 42. Cost Optimization

Cost should be controlled through routing rather than simply using
cheaper models everywhere.

## Use cheap/deterministic processing for:

-   File integrity
-   Page detection
-   MIME detection
-   Duplicate detection
-   Exact identifier search
-   Unit conversion
-   Share arithmetic
-   Date ordering
-   Geometry validity

## Use Flash-class models for:

-   Classification
-   First-pass extraction
-   Basic normalization

## Use stronger models for:

-   Difficult handwritten pages
-   Complex legal reasoning
-   Ambiguous ownership relationships
-   Final synthesis only where required

## Human review for:

-   High-value unresolved ambiguity

------------------------------------------------------------------------

# 43. Model Provider Abstraction

Do not hard-code Gemini-specific response formats throughout the
application.

Define:

``` python
class LLMProvider:
    async def extract(...)
    async def classify(...)
    async def reason(...)
    async def embed(...)
```

Implement:

``` text
GeminiProvider
OpenAIProvider
LocalProvider
MockProvider
```

This allows model replacement without rewriting the domain pipeline.

------------------------------------------------------------------------

# 44. Prompt Versioning

Every AI operation must record:

``` text
prompt_id
prompt_version
model
temperature
schema_version
timestamp
```

Example:

``` text
LegalAgent
model = gemini-2.5-pro
prompt = legal_analysis_v17
schema = legal_finding_v4
```

This is essential for reproducibility.

------------------------------------------------------------------------

# 45. Schema Versioning

Every structured output should have a schema version.

Example:

``` json
{
  "schema": "ownership_event.v3",
  "event_type": "SALE",
  ...
}
```

Never silently change the meaning of existing fields.

------------------------------------------------------------------------

# 46. Database Design

Recommended logical tables:

``` text
tenants

documents
document_pages
document_integrity_checks
document_classifications

page_extractions
evidence_items

persons
person_aliases
person_identifiers

parcels
parcel_identifiers
parcel_geometries
parcel_identifier_events

ownership_events
ownership_event_parties
ownership_shares

facts
fact_evidence

legal_findings
geo_findings
conflicts
missing_evidence

processing_jobs
processing_stage_runs

document_embeddings
conversations
conversation_messages

review_tasks
review_decisions

model_runs
prompt_versions
audit_events
```

------------------------------------------------------------------------

# 47. Facts vs Evidence

Do not store only:

``` text
owner = "Ram Kumar"
```

Store:

``` text
Fact
  owner = person_123
  status = SUPPORTED
      │
      ├── evidence_1
      ├── evidence_7
      └── evidence_19
```

This allows the system to answer:

> "Why do you believe Ram Kumar is the owner?"

with actual evidence.

------------------------------------------------------------------------

# 48. Conflicts as First-Class Data

A conflict should be stored explicitly.

``` json
{
  "conflict_type": "OWNER_NAME_CONFLICT",
  "facts": [
    "fact_123",
    "fact_789"
  ],
  "severity": "HIGH",
  "status": "OPEN"
}
```

Examples:

-   OWNER_NAME_CONFLICT
-   SURVEY_NUMBER_CONFLICT
-   AREA_CONFLICT
-   DATE_CONFLICT
-   SHARE_CONFLICT
-   OWNERSHIP_SEQUENCE_CONFLICT
-   ENCUMBRANCE_STATUS_CONFLICT
-   GIS_IDENTITY_CONFLICT
-   OCR_CONFLICT

------------------------------------------------------------------------

# 49. Missing Evidence

Create explicit missing-evidence records.

Example:

``` text
Current deed references:
Sale Deed 1998

Required supporting document:
Sale Deed 1998

Status:
NOT_PROVIDED
```

Another:

``` text
Mortgage referenced:
YES

Release deed:
NOT_PROVIDED

Current status:
UNRESOLVED
```

This allows the final TSR to generate a verification checklist
automatically.

------------------------------------------------------------------------

# 50. Document Relationship Graph

In addition to ownership graph, maintain a document graph.

``` text
Sale Deed 2020
      │
      ├── references → Sale Deed 2005
      ├── references → Mutation 2020
      └── references → EC 2020
```

This helps detect missing supporting documents.

A document can therefore be:

``` text
PROVIDED
REFERENCED_BUT_MISSING
NOT_REFERENCED
```

------------------------------------------------------------------------

# 51. Document Completeness Score

Instead of one generic document quality score, produce:

``` text
File integrity:       PASS
Page completeness:    WARNING
OCR quality:          GOOD
Ownership evidence:   PARTIAL
Transaction chain:     INCOMPLETE
Encumbrance evidence: INCOMPLETE
GIS evidence:         PROVIDED
```

This is much more useful for a TSR workflow.

------------------------------------------------------------------------

# 52. Testing Strategy

The AI pipeline requires more than normal unit tests.

## 52.1 Deterministic unit tests

Test:

-   Area conversion
-   Share arithmetic
-   Date ordering
-   Survey normalization
-   Identifier normalization
-   Geometry validity
-   Conflict detection
-   Tenant isolation

## 52.2 Golden document tests

Maintain representative documents:

``` text
sale_deed_clean.pdf
sale_deed_scan.pdf
sale_deed_handwritten.pdf
khatauni_hindi.pdf
rtc_kannada.pdf
mutation_conflict.pdf
inheritance_chain.pdf
partition_deed.pdf
mortgage_release.pdf
missing_pages.pdf
```

Store expected critical facts.

## 52.3 Adversarial tests

Intentionally include:

-   Wrong OCR-looking numbers
-   Similar owner names
-   Missing pages
-   Conflicting areas
-   Conflicting survey numbers
-   Stamps covering numbers
-   Rotated pages
-   Duplicate pages
-   Mixed languages
-   False document references

------------------------------------------------------------------------

# 53. Evaluation Metrics

Do not evaluate the system only using overall LLM answer quality.

Track:

### OCR

-   Character accuracy
-   Critical identifier accuracy
-   Name accuracy
-   Number accuracy

### Extraction

-   Precision
-   Recall
-   F1

### Entity resolution

-   False merge rate
-   False split rate

### Ownership

-   Event extraction accuracy
-   Chronology accuracy
-   Share consistency

### GIS

-   Parcel identity accuracy
-   Intersection/overlap accuracy
-   Boundary consistency

### Legal

-   Finding precision
-   Unsupported conclusion rate
-   Missed critical finding rate

### RAG

-   Retrieval recall
-   Evidence citation accuracy
-   Unsupported answer rate

The most important safety metric should be:

> **Unsupported material claim rate**

------------------------------------------------------------------------

# 54. Quality Gates

Before a document can produce a "final" TSR:

``` text
[ ] File integrity acceptable
[ ] Page completeness checked
[ ] Critical OCR conflicts resolved
[ ] Critical identifiers extracted
[ ] Owner identity conflicts reviewed
[ ] Ownership chronology validated
[ ] Share arithmetic validated
[ ] Parcel identity checked
[ ] Area conflicts identified
[ ] Encumbrance evidence evaluated
[ ] Missing supporting documents listed
[ ] High-severity findings reviewed
```

If required gates fail:

``` text
TSR status = DRAFT / REVIEW_REQUIRED
```

rather than:

``` text
TSR status = FINAL
```

------------------------------------------------------------------------

# 55. API Design

Keep the current API compatibility where possible but introduce a
cleaner processing model.

``` text
POST /api/v2/documents
POST /api/v2/jobs
GET  /api/v2/jobs/{id}
GET  /api/v2/documents/{id}
GET  /api/v2/documents/{id}/evidence
GET  /api/v2/documents/{id}/facts
GET  /api/v2/documents/{id}/conflicts
GET  /api/v2/documents/{id}/findings

GET  /api/v2/properties/{id}/ownership
GET  /api/v2/properties/{id}/parcel
GET  /api/v2/properties/{id}/verification

POST /api/v2/query
POST /api/v2/reports/tsr

POST /api/v2/reviews/{id}/decision
```

Existing `/api/v1` endpoints can remain for compatibility.

------------------------------------------------------------------------

# 56. Asynchronous Job Model

Recommended job states:

``` text
QUEUED
VALIDATING
EXTRACTING
NORMALIZING
RECONCILING
VALIDATING_FACTS
REVIEW_REQUIRED
GENERATING
COMPLETED
FAILED
PARTIAL
```

Every stage should expose:

``` text
started_at
completed_at
duration
status
error
retry_count
model_usage
cost
```

------------------------------------------------------------------------

# 57. Observability

Track:

-   Processing duration per page
-   OCR provider
-   Tokens
-   Gemini requests
-   Retry count
-   Cache hits
-   Extraction confidence
-   Conflict rate
-   Human review rate
-   Cost per document
-   Cost per page
-   RAG retrieval quality
-   Unsupported answer rate

Use structured logs with:

``` text
tenant_id
document_id
job_id
stage
trace_id
```

Never log raw sensitive document contents by default.

------------------------------------------------------------------------

# 58. Security

## Tenant isolation

Every query must include tenant context.

Prefer:

``` text
tenant_id
```

as a mandatory database/query boundary.

For PostgreSQL, consider Row-Level Security where practical.

## S3

Use:

``` text
tenants/{tenant_id}/documents/{document_id}/...
```

with private buckets.

Use pre-signed URLs for controlled access.

## API keys

Store only hashes.

Rotate keys.

Support expiration/revocation.

## Sensitive data

Minimize logging of:

-   Aadhaar
-   PAN
-   bank details
-   personal addresses
-   document contents

------------------------------------------------------------------------

# 59. Storage Architecture

Recommended:

### S3

Original and derived files:

``` text
raw/
normalized/
page-images/
ocr/
reports/
exports/
```

### PostgreSQL

Structured metadata, facts, evidence, conflicts, jobs and tenant data.

### pgvector

Embeddings.

### Redis

Use for:

-   Queue support where appropriate
-   Short-lived caching
-   Rate limiting
-   Distributed locks

Avoid making Redis the authoritative source for ownership state.

The authoritative ownership model should live in PostgreSQL.

------------------------------------------------------------------------

# 60. Ownership Graph Storage Recommendation

The current Redis/RedisGraph concept is useful for graph traversal, but
the source of truth should be relational ownership events.

Recommended:

``` text
PostgreSQL
    ↓
ownership_events
    ↓
derived ownership graph
    ↓
optional graph/cache layer
```

Benefits:

-   Transactional consistency
-   Easier auditing
-   Easier historical queries
-   Easier testing
-   Easier migrations
-   Less dependency on a specialized graph backend

------------------------------------------------------------------------

# 61. Failure Handling

Every processing stage should support:

``` text
retry
resume
skip
manual review
partial completion
```

Example:

``` text
OCR successful
NER successful
LegalAgent failed
Ownership successful
GIS successful
```

The document should become:

``` text
PARTIAL
```

rather than silently returning a complete-looking TSR.

------------------------------------------------------------------------

# 62. AI Failure Containment

Never let an LLM directly perform irreversible operations.

LLM output should be:

``` text
proposal
```

then:

``` text
schema validation
        ↓
deterministic validation
        ↓
cross-document validation
        ↓
persist
```

Example:

``` text
LLM says:
area = 2.5 acres

        ↓

Parser:
2.5 acres = 10117.14 m²

        ↓

Other documents:
2.2 acres

        ↓

Conflict engine:
AREA_CONFLICT
```

------------------------------------------------------------------------

# 63. Confidence Calibration

Model confidence values should not be treated as probabilities
automatically.

Instead, calibrate them against a labeled evaluation set.

For example:

``` text
model confidence 0.90
```

should only be considered meaningful if historical evaluation
demonstrates that such predictions are actually reliable.

Until calibration exists, use categorical evidence states:

``` text
HIGH / MEDIUM / LOW
```

combined with deterministic validation.

------------------------------------------------------------------------

# 64. Prompt Design

Prompts should instruct agents to produce:

1.  Fact
2.  Evidence
3.  Confidence
4.  Alternative interpretations
5.  Missing evidence
6.  Conflicts

Example:

``` text
Do not infer a survey number when the source is unreadable.

If two possible readings exist:
- return both candidates
- cite their evidence
- mark the field AMBIGUOUS
- do not select one without supporting evidence.
```

This is more important than simply increasing model size.

------------------------------------------------------------------------

# 65. Agent Architecture

Recommended agents:

``` text
DocumentClassifier
PageInterpreter
EntityExtractor
ParcelExtractor
TransactionExtractor
OwnershipEventExtractor
EncumbranceExtractor
LegalFindingAgent
GeoInterpretationAgent
ReconciliationAgent
SynthesisAgent
```

But deterministic services should sit between them.

Do not make every problem an "agent".

For example:

``` text
Unit conversion → code
Date ordering → code
Share calculation → code
Geometry validity → code
Conflict detection → code
```

AI should handle ambiguity and semantic interpretation.

------------------------------------------------------------------------

# 66. Reconciliation Agent

Introduce a dedicated reconciliation stage.

Input:

``` text
facts
entities
parcels
ownership events
legal findings
GIS findings
```

Output:

``` text
supported facts
conflicts
ambiguities
missing evidence
review tasks
```

This stage should not invent facts. Its job is to compare evidence.

------------------------------------------------------------------------

# 67. Final Synthesis

The SynthesisAgent should consume the reconciled dataset, not raw OCR.

Bad architecture:

``` text
OCR → SynthesisAgent
```

Preferred:

``` text
OCR
 ↓
Evidence
 ↓
Facts
 ↓
Validation
 ↓
Conflicts
 ↓
Findings
 ↓
Synthesis
```

This greatly reduces the chance of a polished but incorrect final
narrative.

------------------------------------------------------------------------

# 68. Recommended Result Object

A final property analysis should resemble:

``` json
{
  "property": {
    "parcel_status": "SUPPORTED"
  },
  "owners": {
    "status": "CONFLICTING",
    "entities": []
  },
  "ownership_chain": {
    "status": "PARTIAL",
    "events": []
  },
  "encumbrances": {
    "status": "UNRESOLVED",
    "findings": []
  },
  "survey_identity": {
    "status": "SUPPORTED"
  },
  "area": {
    "status": "CONFLICTING"
  },
  "gis": {
    "status": "SUPPORTED"
  },
  "missing_evidence": [],
  "review_required": true
}
```

This is much safer than:

``` json
{
  "risk_score": 23,
  "owner": "X",
  "title_clear": true
}
```

------------------------------------------------------------------------

# 69. RAG and Final Dataset Must Share One Truth Layer

Do not let RAG use raw OCR while TSR uses processed facts.

Both should use the same evidence/fact layer.

``` text
                 Evidence / Facts
                   /          \
                  /            \
                 ▼              ▼
             TSR Engine      RAG Engine
```

Therefore:

-   RAG sees conflicts.
-   TSR sees conflicts.
-   Both cite the same source evidence.
-   A correction propagates consistently.

------------------------------------------------------------------------

# 70. Example: Mortgage Failure Prevented

### Input

Sale deed:

``` text
"Property was mortgaged to Bank X in 2014."
```

Current deed:

``` text
"Seller represents property as free from encumbrance."
```

No release deed is uploaded.

### Old architecture could produce

``` text
Risk score: 40
Mortgage: false
```

### Improved architecture produces

``` text
Mortgage reference:
SUPPORTED

Current discharge:
NOT_ESTABLISHED

Conflicting statements:
YES

Missing evidence:
Release deed / current encumbrance verification

Status:
REQUIRES_REVIEW
```

This is the desired behavior.

------------------------------------------------------------------------

# 71. Example: OCR Survey Number Failure Prevented

Page A:

``` text
183/2
```

Page B:

``` text
183/7
```

OCR quality on B is poor.

The system should produce:

``` text
Candidate survey numbers:
183/2
183/7

Conflict:
SURVEY_NUMBER_CONFLICT

Preferred evidence:
Page A — higher OCR confidence

Verification:
REQUIRED
```

It should not silently choose `183/2`.

------------------------------------------------------------------------

# 72. Example: Missing Prior Sale Deed

Current deed says:

``` text
Seller acquired property under Sale Deed No. 1234 dated 2002.
```

Sale Deed 1234 is not provided.

Result:

``` text
Ownership chain:
INCOMPLETE

Missing evidence:
Sale Deed No. 1234 dated 2002

Current ownership:
SUPPORTED by supplied current deed

Historical title:
UNVERIFIED
```

------------------------------------------------------------------------

# 73. Example: Similar Names

Documents contain:

``` text
Ramesh Kumar S/o Mohan
Ramesh Kumar S/o Sohan
```

The system should create two candidate entities.

It must not merge them merely because:

``` text
normalized_name = "ramesh kumar"
```

If insufficient evidence exists:

``` text
ENTITY_IDENTITY_AMBIGUOUS
```

------------------------------------------------------------------------

# 74. Example: Parcel Geometry

Deed:

``` text
Gata 183/2
Area 2.5 acres
```

KML:

``` text
Polygon A
```

BhuNaksha overlay:

``` text
Polygon B
```

If A and B overlap only 55%:

``` text
GEOMETRY_IDENTITY_CONFLICT
```

The system should not state:

> "The KML represents Gata 183/2."

Instead:

> "The supplied KML geometry does not sufficiently match the referenced
> cadastral geometry; verification is required."

------------------------------------------------------------------------

# 75. Deployment Architecture

A practical first production deployment can remain relatively simple:

``` text
                    ALB / API Gateway
                           │
                     FastAPI API
                           │
                    Job Queue
                     /         \
                    /           \
             AI Worker       AI Worker
                    \           /
                     \         /
                      PostgreSQL
                       pgvector
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
             S3          Redis        Metrics
              │
           Gemini
```

Do not prematurely split every module into separate microservices.

Use clear internal module boundaries first.

------------------------------------------------------------------------

# 76. Recommended Python Project Structure

``` text
app/
├── api/
│   ├── v1/
│   └── v2/
│
├── domain/
│   ├── documents/
│   ├── evidence/
│   ├── entities/
│   ├── parcels/
│   ├── ownership/
│   ├── legal/
│   ├── gis/
│   ├── findings/
│   └── review/
│
├── pipeline/
│   ├── ingestion/
│   ├── extraction/
│   ├── normalization/
│   ├── reconciliation/
│   ├── validation/
│   └── synthesis/
│
├── ai/
│   ├── providers/
│   ├── prompts/
│   ├── agents/
│   └── schemas/
│
├── retrieval/
│   ├── hybrid_search.py
│   ├── query_rewriter.py
│   ├── reranker.py
│   └── guardrails.py
│
├── infrastructure/
│   ├── postgres/
│   ├── s3/
│   ├── redis/
│   └── queue/
│
├── reports/
├── observability/
└── tests/
```

------------------------------------------------------------------------

# 77. Migration Strategy From Current System

Do not rewrite the entire application at once.

## Phase 1 --- Evidence layer

Add:

-   document_pages
-   evidence_items
-   extraction provenance
-   source bounding boxes

Keep existing agents.

## Phase 2 --- Structured fact layer

Convert agent outputs into:

``` text
facts
entities
parcels
```

## Phase 3 --- Reconciliation

Add:

-   conflicts
-   missing evidence
-   deterministic validators

## Phase 4 --- Ownership v2

Move from direct graph inference to:

``` text
ownership events → validated graph
```

## Phase 5 --- GIS v2

Separate:

``` text
geometry validity
geometry identity
boundary consistency
```

## Phase 6 --- Review system

Add human verification workflow.

## Phase 7 --- RAG v2

Make RAG consume the validated evidence layer.

## Phase 8 --- TSR v2

Generate reports from validated facts/findings.

------------------------------------------------------------------------

# 78. What Should Be Removed or Reduced

The following patterns should not be relied on as primary mechanisms:

### One giant synthesis prompt

Replace with structured findings.

### Single confidence score

Replace with evidence + verification state.

### Redis as ownership source of truth

Use PostgreSQL ownership events as source of truth.

### LLM arithmetic

Use deterministic calculations.

### LLM geometry validation

Use Shapely/GeoPandas.

### Vector-only identifier retrieval

Use exact + lexical + vector retrieval.

### Automatic entity merging

Use conservative identity resolution.

### "No result" = "not present"

Use explicit `NOT_FOUND` vs `NOT_PROVIDED`.

### FastAPI background tasks for heavy processing

Use durable jobs/workers.

------------------------------------------------------------------------

# 79. Most Important Improvements

If implementation effort is limited, prioritize these in order:

## P0 --- Evidence provenance

Every critical fact must point to document/page/source region.

## P0 --- Unknown/conflict states

Never force ambiguous data into a definitive answer.

## P0 --- Document completeness

Detect missing/suspicious pages.

## P0 --- Critical-field validation

Survey number, owner, area, dates and registration numbers require
stronger validation.

## P0 --- Cross-document reconciliation

Compare documents before synthesis.

## P1 --- Ownership event model

Represent transfers chronologically.

## P1 --- Deterministic validation

Arithmetic, dates, units, geometry and identifiers should not depend on
LLM reasoning.

## P1 --- Human review

Create a review queue for high-impact ambiguity.

## P1 --- Evidence-grounded RAG

Return citations and conflict status.

## P2 --- External verification

Add government/external record checks where available.

------------------------------------------------------------------------

# 80. Final Architecture Philosophy

The improved system should be thought of as:

``` text
                    AI
                     │
                     ▼
             Generate candidates
                     │
                     ▼
                  Evidence
                     │
                     ▼
            Deterministic checks
                     │
                     ▼
          Cross-document comparison
                     │
                     ▼
              Domain reasoning
                     │
                     ▼
              Conflict detection
                     │
                     ▼
               Human review
                     │
                     ▼
              Verified dataset
                     │
             ┌───────┴────────┐
             ▼                ▼
            TSR              RAG
```

The critical architectural change is that **the LLM is no longer the
source of truth**.

It becomes one component in a larger evidence and validation system.

------------------------------------------------------------------------

# 81. Final Quality Model

For every important conclusion, Auxilium should be able to answer four
questions:

### 1. What does the system claim?

``` text
Owner = Ram Kumar
```

### 2. Why does it claim that?

``` text
Sale Deed, page 14
Khatauni, page 7
```

### 3. Is anything contradictory?

``` text
Mutation record identifies another person.
```

### 4. What is still unknown?

``` text
Prior inheritance deed not provided.
Current EC not verified.
```

If the system can answer these four questions for every material TSR
conclusion, it becomes significantly more robust, auditable and useful
than a conventional LLM document-analysis pipeline.

------------------------------------------------------------------------

# 82. Target Outcome

The goal of this architecture is not to make Auxilium say:

> "I am 99% confident."

The goal is to make Auxilium say:

> **"Here is what the supplied records establish, here is the evidence,
> here is where the records disagree, here is what could not be
> established, and here is exactly what should be verified next."**

That is the appropriate architecture for high-stakes Indian land/title
document intelligence.
