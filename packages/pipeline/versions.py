"""Centralized pipeline stage version tuples for selective reprocess."""

from __future__ import annotations

# Bump prompt_version / stage_version here to force selective re-extract
# without wiping evidence. Unchanged (hash, stage, versions) rows still skip.

STAGE_VERSIONS: dict[str, tuple[str, str, str]] = {
    # stage_name -> (stage_version, model_version, prompt_version)
    "integrity": ("1", "", ""),
    "page_split": ("1", "", ""),
    "page_quality": ("1", "", ""),
    "classify": ("1", "mock-or-gemini", "document_classify:1"),
    "ocr": ("1", "mock-or-gemini", "page_ocr:1"),
    "evidence_persist": ("1", "", ""),
    "structured_extract": ("1", "mock-or-gemini", "structured_extract:1"),
    "index_embeddings": ("1", "mock-or-gemini-embed", "embed:1"),
}

# Stages that leave durable evidence; failure after these → partial success
EVIDENCE_COMPLETE_STAGE = "evidence_persist"

AI_STAGES = frozenset({"classify", "ocr", "structured_extract", "index_embeddings"})


def version_tuple(stage_name: str) -> tuple[str, str, str]:
    return STAGE_VERSIONS[stage_name]
