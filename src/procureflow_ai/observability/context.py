"""Per-request and per-invocation context variables for tracing and SSE event streaming."""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any

current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "procureflow_session_id", default=""
)
current_service_name: contextvars.ContextVar[str] = contextvars.ContextVar(
    "procureflow_service_name", default="gateway"
)
current_sse_queue: contextvars.ContextVar[asyncio.Queue[dict[str, Any]] | None] = (
    contextvars.ContextVar("procureflow_sse_queue", default=None)
)


def push_sse_event(event: dict[str, Any]) -> None:
    """Enqueue a real-time SSE event if an active stream queue is bound to this context."""
    queue = current_sse_queue.get()
    if queue is not None:
        try:
            queue.put_nowait(event)
        except Exception:
            pass
