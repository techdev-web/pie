"""Pipeline / job context binding for structured logs."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import structlog


def bind_pipeline_context(**kwargs: Any) -> None:
    """Bind tenant/case/document/job/stage/model_run_id onto structlog contextvars."""
    cleaned = {k: v for k, v in kwargs.items() if v is not None}
    if cleaned:
        structlog.contextvars.bind_contextvars(**cleaned)


def clear_pipeline_context(*keys: str) -> None:
    if keys:
        structlog.contextvars.unbind_contextvars(*keys)
    else:
        structlog.contextvars.clear_contextvars()


@contextmanager
def pipeline_context(**kwargs: Any) -> Iterator[None]:
    bind_pipeline_context(**kwargs)
    try:
        yield
    finally:
        clear_pipeline_context(*kwargs.keys())
