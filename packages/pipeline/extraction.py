"""Phase 2 structured intelligence: extract → normalize → persist facts."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.ai import get_llm_provider
from packages.ai.model_runs import build_model_run
from packages.ai.routing import fact_state_for_ai_failure
from packages.ai.schemas import CandidateFactPayload
from packages.domain.models import (
    Document,
    DocumentClassification,
    EvidenceItem,
    Fact,
    FactEvidence,
    OwnershipEvent,
    OwnershipEventParty,
    OwnershipShare,
    Parcel,
    ParcelIdentifier,
    Person,
    PersonAlias,
    PersonIdentifier,
    ProcessingJob,
)
from packages.domain.storage import sha256_bytes
from packages.domain.textutil import normalize_text
from packages.observability import get_logger
from packages.pipeline.normalization import (
    date_to_utc_datetime,
    normalize_identifier,
    normalize_person_name,
    parse_area,
    parse_date,
    parse_share,
    validate_candidate_fact,
)

log = get_logger("pipeline.extraction")

EXTRACTION_VERSION = "1.0"


def _fact_key(*parts: str) -> str:
    raw = "|".join(parts)
    return f"fact_{sha256_bytes(raw.encode())[:20]}"


def _match_evidence(
    evidence_items: list[EvidenceItem],
    *,
    page_number: int | None,
    snippet: str | None,
    value_text: str | None,
) -> list[EvidenceItem]:
    if not evidence_items:
        return []
    matches: list[EvidenceItem] = []
    snippet_n = normalize_text(snippet or "")
    value_n = normalize_text(value_text or "")

    for ev in evidence_items:
        if page_number is not None and ev.page_number != page_number:
            continue
        hay = ev.normalized_text or normalize_text(ev.text)
        if snippet_n and snippet_n[:80] in hay:
            matches.append(ev)
            continue
        if value_n and value_n in hay:
            matches.append(ev)
            continue
    if matches:
        return matches

    # Fallback: same page, or first evidence if page unknown
    if page_number is not None:
        page_matches = [e for e in evidence_items if e.page_number == page_number]
        if page_matches:
            return page_matches[:1]
    return evidence_items[:1]


async def _get_or_create_person(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    name: str,
    cache: dict[str, Person],
) -> Person:
    key = normalize_person_name(name)
    if key in cache:
        return cache[key]
    existing = (
        await session.execute(
            select(Person).where(Person.case_id == case_id, Person.normalized_name == key)
        )
    ).scalar_one_or_none()
    if existing:
        cache[key] = existing
        return existing
    person = Person(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        case_id=case_id,
        display_name=name.strip(),
        normalized_name=key,
    )
    session.add(person)
    cache[key] = person
    return person


async def stage_structured_extract(
    session: AsyncSession, job: ProcessingJob, document: Document
) -> None:
    """Gemini proposes candidates; code normalizes and persists with evidence links."""
    llm = get_llm_provider()

    evidence_items = list(
        (
            await session.execute(
                select(EvidenceItem)
                .where(
                    EvidenceItem.document_id == document.id,
                    EvidenceItem.case_id == job.case_id,
                )
                .order_by(EvidenceItem.page_number)
            )
        )
        .scalars()
        .all()
    )

    # Idempotent for same extraction version
    existing_facts = (
        await session.execute(
            select(Fact).where(
                Fact.document_id == document.id,
                Fact.case_id == job.case_id,
                Fact.extraction_version == EXTRACTION_VERSION,
            )
        )
    ).scalars().all()
    if existing_facts:
        log.info(
            "structured_extract_skipped",
            document_id=str(document.id),
            existing=len(existing_facts),
        )
        return

    classification = (
        await session.execute(
            select(DocumentClassification).where(DocumentClassification.document_id == document.id)
        )
    ).scalar_one_or_none()
    doc_type = classification.doc_type if classification else "unknown"

    evidence_pages = [
        {
            "page_number": ev.page_number,
            "text": ev.text,
            "evidence_id": ev.evidence_id,
        }
        for ev in evidence_items
    ]

    try:
        t0 = time.perf_counter()
        result = await llm.extract_structured(
            doc_type=doc_type,
            filename=document.source_filename,
            evidence_pages=evidence_pages,
        )
        t1 = time.perf_counter()
    except Exception as exc:
        log.exception("structured_extract_ai_failed", error=str(exc))
        # Keep evidence; mark a sentinel fact for human review — never silent fill-in
        session.add(
            Fact(
                id=uuid.uuid4(),
                fact_id=f"fact_{uuid.uuid4().hex[:12]}",
                tenant_id=job.tenant_id,
                case_id=job.case_id,
                document_id=document.id,
                fact_type="extraction.failure",
                predicate="ai_failed",
                value_text=str(exc)[:500],
                verification_state=fact_state_for_ai_failure(),
                confidence=0.0,
                value_json={"error": str(exc)[:500]},
            )
        )
        await session.commit()
        raise

    model_run = build_model_run(
        tenant_id=job.tenant_id,
        case_id=job.case_id,
        document_id=document.id,
        stage="structured_extract",
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

    person_cache: dict[str, Person] = {}
    for person_payload in result.persons:
        person = await _get_or_create_person(
            session,
            tenant_id=job.tenant_id,
            case_id=job.case_id,
            name=person_payload.name,
            cache=person_cache,
        )
        for alias in person_payload.aliases:
            if normalize_person_name(alias) == person.normalized_name:
                continue
            session.add(
                PersonAlias(
                    id=uuid.uuid4(),
                    person_id=person.id,
                    tenant_id=job.tenant_id,
                    alias=alias.strip(),
                    normalized_alias=normalize_person_name(alias),
                    source="extraction",
                )
            )
        for ident in person_payload.identifiers:
            session.add(
                PersonIdentifier(
                    id=uuid.uuid4(),
                    person_id=person.id,
                    tenant_id=job.tenant_id,
                    id_type=ident.get("id_type") or "unknown",
                    id_value=ident.get("id_value") or "",
                    normalized_value=normalize_identifier(ident.get("id_value") or ""),
                )
            )

    parcels_created: list[Parcel] = []
    for parcel_payload in result.parcels:
        label = parcel_payload.label
        if not label and parcel_payload.identifiers:
            first = parcel_payload.identifiers[0]
            label = f"{first.get('id_type', 'parcel')} {first.get('id_value', '')}".strip()
        label = label or "Unknown parcel"
        parcel = Parcel(
            id=uuid.uuid4(),
            tenant_id=job.tenant_id,
            case_id=job.case_id,
            display_label=label,
        )
        session.add(parcel)
        await session.flush()
        for ident in parcel_payload.identifiers:
            session.add(
                ParcelIdentifier(
                    id=uuid.uuid4(),
                    parcel_id=parcel.id,
                    tenant_id=job.tenant_id,
                    id_type=ident.get("id_type") or "survey",
                    id_value=ident.get("id_value") or "",
                    normalized_value=normalize_identifier(ident.get("id_value") or ""),
                )
            )
        parcels_created.append(parcel)

    primary_parcel = parcels_created[0] if parcels_created else None

    for event_payload in result.ownership_events:
        parsed = parse_date(event_payload.event_date_raw)
        event = OwnershipEvent(
            id=uuid.uuid4(),
            tenant_id=job.tenant_id,
            case_id=job.case_id,
            document_id=document.id,
            parcel_id=primary_parcel.id if primary_parcel else None,
            event_type=event_payload.event_type or "sale",
            event_date=date_to_utc_datetime(parsed.parsed),
            event_date_raw=event_payload.event_date_raw,
            registration_number=event_payload.registration_number,
            verification_state="AMBIGUOUS" if parsed.ambiguous else "EXTRACTED",
            model_run_id=model_run.id,
        )
        session.add(event)
        await session.flush()

        for role, names in (
            ("seller", event_payload.seller_names),
            ("buyer", event_payload.buyer_names),
        ):
            seen_roles: set[str] = set()
            for name in names:
                nkey = normalize_person_name(name)
                if not nkey or nkey in seen_roles:
                    continue
                seen_roles.add(nkey)
                person = await _get_or_create_person(
                    session,
                    tenant_id=job.tenant_id,
                    case_id=job.case_id,
                    name=name,
                    cache=person_cache,
                )
                session.add(
                    OwnershipEventParty(
                        id=uuid.uuid4(),
                        ownership_event_id=event.id,
                        person_id=person.id,
                        role=role,
                    )
                )

        if event_payload.share_text:
            num, den, text = parse_share(event_payload.share_text)
            session.add(
                OwnershipShare(
                    id=uuid.uuid4(),
                    ownership_event_id=event.id,
                    share_numerator=num,
                    share_denominator=den,
                    share_text=text,
                )
            )

    # Ensure facts cover core fields when LLM returns entities but sparse facts
    facts_payload = list(result.facts)
    if not facts_payload and result.persons:
        for p in result.persons:
            if p.role in ("seller", "buyer", "owner"):
                facts_payload.append(
                    CandidateFactPayload(
                        fact_type=f"party.{p.role}",
                        predicate=f"{p.role}_name",
                        value_text=p.name,
                        confidence=0.7,
                    )
                )

    persisted = 0
    for candidate in facts_payload:
        cand_dict: dict[str, Any] = {
            "fact_type": candidate.fact_type,
            "predicate": candidate.predicate,
            "value_text": candidate.value_text,
            "value_normalized": candidate.value_normalized,
            "verification_state": candidate.verification_state or "EXTRACTED",
        }
        errors = validate_candidate_fact(cand_dict)
        if errors:
            log.warning("candidate_fact_rejected", errors=errors, fact_type=candidate.fact_type)
            continue

        value_text = candidate.value_text
        value_normalized = candidate.value_normalized
        value_json: dict[str, Any] | None = dict(candidate.attributes) if candidate.attributes else None
        unit = candidate.unit
        state = candidate.verification_state or "EXTRACTED"

        if candidate.fact_type in ("parcel.survey_number",) or candidate.predicate in (
            "survey_or_gata",
            "survey_number",
            "gata",
        ):
            if value_text:
                value_normalized = normalize_identifier(value_text)
        elif candidate.fact_type == "parcel.area" or candidate.predicate == "area":
            area = parse_area(value_text)
            if area.value is not None:
                value_normalized = str(area.sqm) if area.sqm is not None else str(area.value)
                unit = "sqm" if area.sqm is not None else area.unit
                value_json = {
                    **(value_json or {}),
                    "raw": area.raw,
                    "value": area.value,
                    "unit": area.unit,
                    "sqm": area.sqm,
                    "ambiguous": area.ambiguous,
                }
                if area.ambiguous:
                    state = "AMBIGUOUS"
        elif candidate.fact_type == "transaction.date" or candidate.predicate == "deed_date":
            parsed = parse_date(value_text)
            value_normalized = parsed.iso
            value_json = {
                **(value_json or {}),
                "raw": parsed.raw,
                "ambiguous": parsed.ambiguous,
            }
            if parsed.ambiguous:
                state = "AMBIGUOUS"
            elif parsed.parsed is None and state == "EXTRACTED":
                state = "REQUIRES_REVIEW"
        elif candidate.fact_type.startswith("party.") and value_text:
            value_normalized = normalize_person_name(value_text)

        subject_type = None
        subject_id = None
        if candidate.fact_type.startswith("party.") and value_text:
            person = await _get_or_create_person(
                session,
                tenant_id=job.tenant_id,
                case_id=job.case_id,
                name=value_text,
                cache=person_cache,
            )
            subject_type = "person"
            subject_id = person.id
        elif candidate.fact_type.startswith("parcel.") and primary_parcel:
            subject_type = "parcel"
            subject_id = primary_parcel.id

        evidence_links = []
        if state not in ("NOT_FOUND", "NOT_PROVIDED", "NOT_APPLICABLE"):
            evidence_links = _match_evidence(
                evidence_items,
                page_number=candidate.page_number,
                snippet=candidate.evidence_snippet,
                value_text=value_text,
            )
            if not evidence_links:
                # No fact without evidence — downgrade to NOT_FOUND scoped to docs
                state = "NOT_FOUND"
                value_json = {
                    **(value_json or {}),
                    "scope": "supplied_documents",
                    "reason": "no_evidence_link",
                }

        fact = Fact(
            id=uuid.uuid4(),
            fact_id=_fact_key(
                str(job.case_id),
                str(document.id),
                candidate.fact_type,
                candidate.predicate,
                value_normalized or value_text or state,
                EXTRACTION_VERSION,
            ),
            tenant_id=job.tenant_id,
            case_id=job.case_id,
            document_id=document.id,
            fact_type=candidate.fact_type,
            subject_type=subject_type,
            subject_id=subject_id,
            predicate=candidate.predicate,
            value_text=value_text,
            value_normalized=value_normalized,
            value_json=value_json,
            unit=unit,
            verification_state=state,
            confidence=candidate.confidence,
            extraction_version=EXTRACTION_VERSION,
            model_run_id=model_run.id,
        )
        session.add(fact)
        await session.flush()

        for ev in evidence_links:
            session.add(
                FactEvidence(
                    id=uuid.uuid4(),
                    fact_id=fact.id,
                    evidence_item_id=ev.id,
                    link_role="supports",
                )
            )
        persisted += 1

    await session.commit()
    log.info(
        "structured_extract_persisted",
        document_id=str(document.id),
        facts=persisted,
        persons=len(person_cache),
        parcels=len(parcels_created),
    )
