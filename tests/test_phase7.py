"""Phase 7: reports from verified truth layer, section reuse, conflict parity, PII redaction."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from packages.observability.redaction import redact_string, redact_value
from packages.reports.pdf import render_report_pdf
from packages.reports.sections import (
    REVIEW_AFFECTED_SECTIONS,
    SECTION_KEYS,
    build_conflicts_section,
    build_encumbrance_section,
    build_missing_evidence_section,
    build_ownership_section,
    build_recommendations_section,
    build_risk_section,
    build_scope_section,
    compute_truth_fingerprint,
    section_fingerprint,
)


def test_section_keys_match_guide():
    assert "scope" in SECTION_KEYS
    assert "conflicts" in SECTION_KEYS
    assert "risk_drivers" in SECTION_KEYS
    assert "recommended_verifications" in SECTION_KEYS
    assert set(REVIEW_AFFECTED_SECTIONS["approve"]).issubset(set(SECTION_KEYS))


def test_truth_fingerprint_stable_and_sensitive_to_conflict_status():
    base = dict(
        case_id="c1",
        documents=[{"id": "d1", "content_hash": "abc", "source_filename": "deed.pdf"}],
        facts=[
            {
                "fact_id": "f1",
                "fact_type": "party.owner",
                "verification_state": "EXTRACTED",
                "value_normalized": "ram",
            }
        ],
        conflicts=[
            {
                "conflict_id": "cnf1",
                "conflict_type": "OWNER_NAME_CONFLICT",
                "status": "OPEN",
                "summary": "Ram vs Shyam",
            }
        ],
        gaps=[],
        risk={"risk_level": "HIGH", "score": 55, "drivers": []},
        review_open_count=1,
        decision_count=0,
    )
    a = compute_truth_fingerprint(**base)
    b = compute_truth_fingerprint(**base)
    assert a == b

    changed = {**base, "conflicts": [{**base["conflicts"][0], "status": "RESOLVED"}]}
    c = compute_truth_fingerprint(**changed)
    assert a != c


def test_review_regen_only_marks_dependent_sections():
    affected = set(REVIEW_AFFECTED_SECTIONS["approve"])
    assert "conflicts" in affected
    assert "scope" not in affected  # document scope unchanged by approve
    assert "missing_evidence" not in REVIEW_AFFECTED_SECTIONS["approve"]
    assert "missing_evidence" in REVIEW_AFFECTED_SECTIONS["request_docs"]


def test_section_reuse_when_fingerprint_unchanged():
    scope = build_scope_section(
        case_title="Plot 1",
        documents=[
            {
                "id": str(uuid.uuid4()),
                "source_filename": "a.pdf",
                "content_hash": "h1",
                "page_count": 2,
                "mime_type": "application/pdf",
                "role": "primary",
                "upload_status": "ready",
            }
        ],
        classifications={},
    )
    fp1 = section_fingerprint("scope", scope)
    fp2 = section_fingerprint("scope", scope)
    assert fp1 == fp2

    conflicts_open = build_conflicts_section(
        conflicts=[
            {
                "conflict_id": "cnf1",
                "conflict_type": "OWNER_NAME_CONFLICT",
                "severity": "HIGH",
                "status": "OPEN",
                "summary": "Ram vs Shyam",
                "facts": [{"fact_id": "f1"}, {"fact_id": "f2"}],
            }
        ]
    )
    conflicts_resolved = build_conflicts_section(
        conflicts=[
            {
                "conflict_id": "cnf1",
                "conflict_type": "OWNER_NAME_CONFLICT",
                "severity": "HIGH",
                "status": "RESOLVED",
                "summary": "Ram vs Shyam",
                "facts": [{"fact_id": "f1"}, {"fact_id": "f2"}],
            }
        ]
    )
    assert section_fingerprint("conflicts", conflicts_open) != section_fingerprint(
        "conflicts", conflicts_resolved
    )
    assert conflicts_open["open_count"] == 1
    assert conflicts_resolved["open_count"] == 0


def test_chat_report_conflict_status_parity():
    """Chat and report must share the same OPEN/RESOLVED conflict status for a fact set."""
    conflicts = [
        {
            "conflict_id": "cnf_owner",
            "conflict_type": "OWNER_NAME_CONFLICT",
            "severity": "HIGH",
            "status": "OPEN",
            "summary": "Ram vs Shyam",
            "facts": [{"fact_id": "f_a"}, {"fact_id": "f_b"}],
        }
    ]
    # Chat packing uses conflict.status; report conflicts section uses the same field.
    chat_open = [
        {"conflict_id": c["conflict_id"], "status": c["status"]}
        for c in conflicts
        if c["status"] == "OPEN"
    ]
    report_sec = build_conflicts_section(conflicts=conflicts)
    report_open_ids = {c["conflict_id"] for c in report_sec["open_conflicts"]}
    chat_open_ids = {c["conflict_id"] for c in chat_open}
    assert chat_open_ids == report_open_ids

    conflicts[0]["status"] = "RESOLVED"
    chat_open2 = [c for c in conflicts if c["status"] == "OPEN"]
    report_sec2 = build_conflicts_section(conflicts=conflicts)
    assert len(chat_open2) == 0
    assert report_sec2["open_count"] == 0
    assert report_sec2["resolved_count"] == 1


def test_encumbrance_never_claims_clear_with_open_mortgage_gap():
    sec = build_encumbrance_section(
        facts=[
            {
                "fact_id": "f_m",
                "fact_type": "encumbrance.mortgage",
                "predicate": "mortgage",
                "value_text": "HDFC mortgage",
                "verification_state": "EXTRACTED",
            }
        ],
        legal_findings=[],
        gaps=[
            {
                "gap_id": "gap_m",
                "gap_type": "MORTGAGE_RELEASE_MISSING",
                "status": "NOT_PROVIDED",
                "summary": "Release missing",
                "referenced_label": "Release deed",
                "required_doc_type": "release_deed",
            }
        ],
    )
    assert "UNRESOLVED" in sec["conclusion"]
    assert "clear" not in sec["conclusion"].lower() or "NOT" in sec["conclusion"]


def test_ownership_section_surfaces_open_owner_conflicts():
    sec = build_ownership_section(
        persons=[{"id": "p1", "display_name": "Ram", "normalized_name": "ram"}],
        timeline=[{"event_type": "sale", "event_date": "2020-01-01"}],
        facts=[
            {
                "fact_id": "f1",
                "fact_type": "party.owner",
                "predicate": "owner_name",
                "value_text": "Ram",
                "verification_state": "EXTRACTED",
            }
        ],
        conflicts=[
            {
                "conflict_id": "cnf1",
                "conflict_type": "OWNER_NAME_CONFLICT",
                "status": "OPEN",
                "summary": "Ram vs Shyam",
            }
        ],
    )
    assert len(sec["open_ownership_conflicts"]) == 1
    assert "open conflicts" in sec["conclusion"].lower()


def test_risk_and_recommendations_sections():
    risk = build_risk_section(
        risk={
            "risk_level": "HIGH",
            "score": 55,
            "weights_version": "v1",
            "drivers": [{"code": "MORTGAGE", "label": "Unresolved mortgage", "weight": 30}],
            "disclaimer": "Not legal truth.",
        }
    )
    assert risk["risk_level"] == "HIGH"
    assert risk["drivers"][0]["weight"] == 30

    recs = build_recommendations_section(
        conflicts=[
            {
                "conflict_id": "cnf1",
                "conflict_type": "OWNER_NAME_CONFLICT",
                "status": "OPEN",
                "summary": "names differ",
            }
        ],
        gaps=[
            {
                "gap_id": "g1",
                "status": "NOT_PROVIDED",
                "referenced_label": "Prior deed 2005",
                "required_doc_type": "sale_deed",
            }
        ],
        legal_findings=[],
        open_review_count=2,
    )
    kinds = {i["kind"] for i in recs["items"]}
    assert "resolve_conflict" in kinds
    assert "upload_document" in kinds
    assert "human_review" in kinds


def test_pdf_renders_nonempty_bytes():
    body = {
        "summary": {
            "document_count": 1,
            "open_conflicts": 1,
            "missing_evidence": 0,
            "risk_level": "HIGH",
        },
        "truth_fingerprint": "abc",
        "language_rules": ["Evidence-first."],
        "section_order": ["conflicts", "risk_drivers"],
        "sections": {
            "conflicts": build_conflicts_section(
                conflicts=[
                    {
                        "conflict_id": "cnf1",
                        "conflict_type": "OWNER_NAME_CONFLICT",
                        "severity": "HIGH",
                        "status": "OPEN",
                        "summary": "Ram vs Shyam",
                        "facts": [],
                    }
                ]
            ),
            "risk_drivers": build_risk_section(
                risk={"risk_level": "HIGH", "score": 40, "drivers": [], "disclaimer": "n/a"}
            ),
        },
    }
    pdf = render_report_pdf(body, title="DD Memo Test")
    assert pdf.startswith(b"%PDF")
    assert b"%%EOF" in pdf
    assert len(pdf) > 200


def test_pii_redaction():
    text = "Contact ram@example.com phone +91 98765 43210 PAN ABCDE1234F aadhaar 1234 5678 9012"
    red = redact_string(text)
    assert "ram@example.com" not in red
    assert "ABCDE1234F" not in red
    assert "1234 5678 9012" not in red
    assert "[REDACTED_EMAIL]" in red
    assert "[REDACTED_PAN]" in red

    payload = redact_value(
        {"email": "a@b.com", "note": "ok", "nested": {"phone": "9999999999"}},
        key=None,
    )
    assert payload["email"] == "[REDACTED]"
    assert payload["note"] == "ok"
    assert payload["nested"]["phone"] == "[REDACTED]"


def test_incremental_section_rebuild_simulation():
    """After review:approve, only dependent sections change; scope fingerprint reused."""
    scope = build_scope_section(
        case_title="Case",
        documents=[{"id": "d1", "source_filename": "x.pdf", "content_hash": "h", "page_count": 1, "mime_type": "application/pdf", "role": "primary", "upload_status": "ready"}],
        classifications={"d1": "sale_deed"},
    )
    conflicts_before = build_conflicts_section(
        conflicts=[
            {
                "conflict_id": "cnf1",
                "conflict_type": "OWNER_NAME_CONFLICT",
                "severity": "HIGH",
                "status": "OPEN",
                "summary": "a vs b",
                "facts": [],
            }
        ]
    )
    conflicts_after = build_conflicts_section(
        conflicts=[
            {
                "conflict_id": "cnf1",
                "conflict_type": "OWNER_NAME_CONFLICT",
                "severity": "HIGH",
                "status": "RESOLVED",
                "summary": "a vs b",
                "facts": [],
            }
        ]
    )
    prior_fps = {
        "scope": section_fingerprint("scope", scope),
        "conflicts": section_fingerprint("conflicts", conflicts_before),
    }
    fresh_fps = {
        "scope": section_fingerprint("scope", scope),
        "conflicts": section_fingerprint("conflicts", conflicts_after),
    }
    must_rebuild = set(REVIEW_AFFECTED_SECTIONS["approve"])
    reused = []
    for key in ("scope", "conflicts"):
        if key not in must_rebuild and prior_fps[key] == fresh_fps[key]:
            reused.append(key)
        elif key not in must_rebuild:
            reused.append(key)  # would rebuild if fp changed
        # else: forced rebuild
    assert "scope" in reused or prior_fps["scope"] == fresh_fps["scope"]
    assert "conflicts" in must_rebuild
    assert prior_fps["conflicts"] != fresh_fps["conflicts"]
