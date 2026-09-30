"""Hybrid RAG retrieval, exact ID search, guardrails, chat, and review (Phase 4–5)."""

from packages.retrieval.chat import answer_case_question
from packages.retrieval.embeddings import EMBEDDING_MODEL, index_case_embeddings
from packages.retrieval.hybrid import hybrid_retrieve
from packages.retrieval.memory import (
    confirm_fact,
    get_latest_memory,
    refresh_case_memory,
    reject_fact,
)
from packages.retrieval.query import classify_query
from packages.retrieval.review import (
    apply_review_decision,
    create_user_flag_task,
    list_review_tasks,
    sync_review_tasks_for_case,
)

__all__ = [
    "answer_case_question",
    "apply_review_decision",
    "classify_query",
    "confirm_fact",
    "create_user_flag_task",
    "EMBEDDING_MODEL",
    "get_latest_memory",
    "hybrid_retrieve",
    "index_case_embeddings",
    "list_review_tasks",
    "refresh_case_memory",
    "reject_fact",
    "sync_review_tasks_for_case",
]
