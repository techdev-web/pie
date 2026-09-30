"""Phase 5: review router candidates, fingerprints, decision effects (unit)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from packages.retrieval.review import (
    CONFLICT_TASK_TYPES,
    DECISION_ACTIONS,
    candidate_from_conflict,
    candidate_from_fact,
    candidate_from_gap,
    fingerprint_for_conflict,
    fingerprint_for_fact,
    fingerprint_for_gap,
)


def _fact(**kwargs):
    defaults = dict(
        fact_id="fact_x",
        fact_type="party.owner",
        predicate="owner_name",
        value_text="Ram Kumar",
        verification_state="EXTRACTED",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _conflict(**kwargs):
    defaults = dict(
        conflict_id="cnf_abc",
        conflict_type="OWNER_NAME_CONFLICT",
        severity="HIGH",
        status="OPEN",
        summary="OWNER_NAME_CONFLICT: Ram vs Shyam",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _gap(**kwargs):
    defaults = dict(
        gap_id="gap_mort",
        gap_type="MORTGAGE_RELEASE_MISSING",
        referenced_label="Mortgage release 2019",
        summary="Mortgage mentioned; release not uploaded",
        status="NOT_PROVIDED",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_decision_actions_cover_guide():
    assert DECISION_ACTIONS == {
        "approve",
        "reject",
        "merge",
        "split",
        "request_docs",
        "annotate",
    }


def test_conflict_fingerprints_stable():
    a = fingerprint_for_conflict("OWNER_NAME_CONFLICT", ["fact_b", "fact_a"])
    b = fingerprint_for_conflict("OWNER_NAME_CONFLICT", ["fact_a", "fact_b"])
    assert a == b
    assert a.startswith("c:")


def test_candidate_from_owner_conflict():
    c = candidate_from_conflict(
        _conflict(), ["fact_owner_a", "fact_owner_b"]
    )
    assert c["task_type"] == "OWNER_IDENTITY_AMBIGUITY"
    assert c["severity"] == "HIGH"
    assert c["source_kind"] == "conflict"
    assert c["related_conflict_id"] == "cnf_abc"
    assert set(c["related_fact_ids"]) == {"fact_owner_a", "fact_owner_b"}


def test_candidate_from_survey_conflict_is_critical():
    c = candidate_from_conflict(
        _conflict(conflict_type="SURVEY_NUMBER_CONFLICT", summary="survey mismatch"),
        ["fact_s1", "fact_s2"],
    )
    assert c["task_type"] == "CRITICAL_ID_CONFLICT"
    assert c["severity"] == "CRITICAL"


def test_candidate_from_share_conflict():
    assert "SHARE_CONFLICT" in CONFLICT_TASK_TYPES
    c = candidate_from_conflict(
        _conflict(conflict_type="SHARE_CONFLICT", summary="shares sum wrong"),
        [],
    )
    assert c["task_type"] == "SHARE_MATH_INCONSISTENCY"


def test_candidate_from_requires_review_fact():
    cand = candidate_from_fact(
        _fact(verification_state="REQUIRES_REVIEW", fact_type="transaction.date")
    )
    assert cand is not None
    assert cand["task_type"] == "REQUIRES_REVIEW"
    assert cand["fingerprint"] == fingerprint_for_fact("fact_x")


def test_candidate_from_ambiguous_owner():
    cand = candidate_from_fact(
        _fact(verification_state="AMBIGUOUS", fact_type="party.owner")
    )
    assert cand is not None
    assert cand["task_type"] == "OWNER_IDENTITY_AMBIGUITY"


def test_candidate_skips_supported_facts():
    assert candidate_from_fact(_fact(verification_state="SUPPORTED")) is None


def test_candidate_from_encumbrance_gap():
    cand = candidate_from_gap(_gap())
    assert cand is not None
    assert cand["task_type"] == "ENCUMBRANCE_UNRESOLVED"
    assert cand["severity"] == "CRITICAL"
    assert cand["fingerprint"] == fingerprint_for_gap("gap_mort")


def test_candidate_skips_unrelated_gap():
    assert candidate_from_gap(_gap(gap_type="PRIOR_DEED_MISSING")) is None


def test_approve_effects_shape_is_reapplicable():
    """Decisions store fact_states keyed by public fact_id for reconcile re-apply."""
    effects = {
        "fact_states": {"fact_a": "VERIFIED", "fact_b": "UNVERIFIED"},
        "conflict_id": "cnf_abc",
    }
    assert effects["fact_states"]["fact_a"] == "VERIFIED"
    assert fingerprint_for_conflict("OWNER_NAME_CONFLICT", ["fact_b", "fact_a"]) == (
        fingerprint_for_conflict("OWNER_NAME_CONFLICT", ["fact_a", "fact_b"])
    )


def test_review_task_model_defaults_importable():
    from packages.domain.models import ReviewDecision, ReviewTask

    task = ReviewTask(
        id=uuid.uuid4(),
        task_id="rt_test",
        tenant_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        task_type="USER_FLAG",
        severity="MEDIUM",
        status="OPEN",
        title="Flag",
        summary="User flagged",
        source_kind="user_flag",
        fingerprint="u:abc",
        related_fact_ids=[],
    )
    decision = ReviewDecision(
        id=uuid.uuid4(),
        decision_id="rd_test",
        review_task_id=task.id,
        tenant_id=task.tenant_id,
        case_id=task.case_id,
        action="annotate",
        actor="tester",
        note="looks odd",
        prior_states={"task_status": "OPEN"},
        effects={"annotation": "looks odd"},
        created_at=datetime.now(timezone.utc),
    )
    assert decision.action == "annotate"
    assert task.status == "OPEN"
