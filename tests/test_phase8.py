"""Phase 8: cost routing, eval gates, reprocess versions, ops metrics helpers."""

from __future__ import annotations

from decimal import Decimal

import pytest

from packages.ai.pricing import estimate_cost_usd
from packages.ai.routing import RouteEngine, model_for_route, route_for_stage
from packages.eval.metrics import (
    citation_precision,
    conflict_recall,
    fact_match_score,
    gate_passed,
    id_retrieval_hit_rate,
)
from packages.eval.runner import run_eval_pack
from packages.pipeline.versions import STAGE_VERSIONS


def test_route_code_for_integrity():
    assert route_for_stage("integrity") == RouteEngine.CODE


def test_route_flash_for_classify_ocr():
    assert route_for_stage("classify") == RouteEngine.FLASH
    assert route_for_stage("ocr") == RouteEngine.FLASH


def test_route_pro_for_ownership_chat_and_legal():
    assert route_for_stage("legal_layer2") == RouteEngine.PRO
    assert (
        route_for_stage("chat", risk_signals={"query_class": "ownership"})
        == RouteEngine.PRO
    )
    assert (
        route_for_stage("chat", risk_signals={"conflicts": True}) == RouteEngine.PRO
    )


def test_route_human_on_ai_failure():
    assert route_for_stage("chat", risk_signals={"ai_failed": True}) == RouteEngine.HUMAN
    assert (
        route_for_stage("classify", risk_signals={"material_unresolved_risk": True})
        == RouteEngine.HUMAN
    )


def test_model_for_route_resolves_settings():
    assert model_for_route(RouteEngine.CODE) is None
    assert model_for_route(RouteEngine.HUMAN) is None
    flash = model_for_route(RouteEngine.FLASH, stage="classify")
    assert flash and "flash" in flash
    pro = model_for_route(RouteEngine.PRO, stage="chat")
    assert pro and "pro" in pro


def test_estimate_cost_usd_flash_vs_pro():
    flash = estimate_cost_usd("gemini-2.0-flash", 1_000_000, 1_000_000)
    pro = estimate_cost_usd("gemini-2.0-pro", 1_000_000, 1_000_000)
    assert flash is not None and pro is not None
    assert pro > flash
    assert flash == Decimal("0.500000")  # 0.10 + 0.40


def test_stage_versions_centralized():
    assert "structured_extract" in STAGE_VERSIONS
    assert STAGE_VERSIONS["ocr"][2].startswith("page_ocr")


def test_citation_and_conflict_metrics():
    cite = citation_precision(
        [
            {"evidence_ids": ["e1"], "requires_citation": True},
            {"evidence_ids": ["missing"], "requires_citation": True},
        ],
        {"e1"},
    )
    assert cite["precision"] == 0.5
    conflict = conflict_recall(
        ["OWNER_NAME_CONFLICT"],
        [{"conflict_type": "OWNER_NAME_CONFLICT"}],
    )
    assert conflict["recall"] == 1.0
    facts = fact_match_score(
        [{"fact_type": "party.seller", "value_text": "Ram"}],
        [{"fact_type": "party.seller", "value_text": "Ram"}],
    )
    assert facts["f1"] == 1.0
    ids = id_retrieval_hit_rate(
        [{"query": "q", "expected_id": "183/2"}],
        [{"query": "q", "hit_ids": ["183/2"]}],
    )
    assert ids["hit_rate"] == 1.0


def test_gate_blocks_citation_regression():
    assert gate_passed(
        {
            "citation_precision": 0.95,
            "conflict_recall": 0.9,
            "hallucination_rate": 0.0,
            "fact_f1": 0.8,
        }
    )
    assert not gate_passed(
        {
            "citation_precision": 0.5,
            "conflict_recall": 0.9,
            "hallucination_rate": 0.0,
            "fact_f1": 0.8,
        }
    )


@pytest.mark.asyncio
async def test_golden_pack_gate():
    result = await run_eval_pack(None, pack_name="golden", persist=False)
    assert result["gate_passed"] is True
    assert result["metrics"]["citation_precision"] >= 0.9


@pytest.mark.asyncio
async def test_adversarial_pack_gate():
    result = await run_eval_pack(None, pack_name="adversarial", persist=False)
    assert result["gate_passed"] is True
    assert "dual_survey" in [c["fixture_id"] for c in result["details"]["cases"]]
