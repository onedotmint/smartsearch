"""HTTP client helpers used by retained provider adapters."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import httpx


def current_context() -> None:
    """v1 entrypoints do not install a shared request context."""
    return None


@asynccontextmanager
async def request_client(context: Any = None, **client_kwargs: Any):
    del context
    async with httpx.AsyncClient(**client_kwargs) as client:
        yield client


def request_timeout_kwargs(default: float | None = None, context: Any = None) -> dict[str, Any]:
    del default, context
    return {}


def bounded_retry_delay(delay: float, context: Any = None) -> float:
    del context
    return max(0.0, float(delay or 0.0))
