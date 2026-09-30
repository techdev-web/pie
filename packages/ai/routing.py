"""Cost-aware model routing: code → Flash → Pro → human."""

from __future__ import annotations

from enum import Enum
from typing import Any

from packages.config import Settings, get_settings


class RouteEngine(str, Enum):
    CODE = "code"
    FLASH = "flash"
    PRO = "pro"
    HUMAN = "human"


# Stages that must never call an LLM
CODE_STAGES = frozenset(
    {
        "integrity",
        "page_split",
        "page_quality",
        "evidence_persist",
        "normalization",
        "reconciliation",
        "conflict_rules",
        "share_math",
        "geometry",
        "dates",
        "units",
    }
)

FLASH_STAGES = frozenset({"classify", "ocr", "structured_extract", "embed", "index_embeddings"})

PRO_STAGES = frozenset({"chat_ownership", "legal_layer2", "hard_reconciliation_narrative"})


def route_for_stage(
    stage: str,
    *,
    risk_signals: dict[str, Any] | None = None,
) -> RouteEngine:
    """Select engine for a pipeline/chat stage.

    Material unresolved risk or explicit AI failure → HUMAN.
    Legal ambiguity / ownership chat with conflicts → PRO.
    Classify/OCR/bulk extract → FLASH.
    Deterministic stages → CODE.
    """
    signals = risk_signals or {}
    if signals.get("requires_human") or signals.get("ai_failed"):
        return RouteEngine.HUMAN
    if signals.get("material_unresolved_risk"):
        return RouteEngine.HUMAN

    if stage in CODE_STAGES:
        return RouteEngine.CODE

    if stage in PRO_STAGES:
        return RouteEngine.PRO

    if stage == "chat":
        if signals.get("conflicts") or signals.get("query_class") == "ownership":
            return RouteEngine.PRO
        if signals.get("legal_ambiguity"):
            return RouteEngine.PRO
        return RouteEngine.FLASH

    if stage in FLASH_STAGES:
        return RouteEngine.FLASH

    # Default bulk AI work to Flash
    return RouteEngine.FLASH


def model_for_route(
    route: RouteEngine,
    *,
    stage: str | None = None,
    settings: Settings | None = None,
) -> str | None:
    """Resolve concrete model name. None for CODE/HUMAN (no LLM call)."""
    if route in (RouteEngine.CODE, RouteEngine.HUMAN):
        return None
    settings = settings or get_settings()
    if route == RouteEngine.PRO:
        return settings.gemini_pro_model
    # FLASH
    if stage == "ocr":
        return settings.gemini_ocr_model
    if stage in ("embed", "index_embeddings"):
        return settings.gemini_embed_model
    if stage == "chat":
        return settings.gemini_chat_model
    return settings.gemini_classify_model


def answer_status_for_human_route() -> str:
    return "INSUFFICIENT_EVIDENCE"


def fact_state_for_ai_failure() -> str:
    return "REQUIRES_REVIEW"
