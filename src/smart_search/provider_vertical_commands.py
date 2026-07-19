"""AnySearch vertical provider command ownership."""

import time
from typing import Any

from .config import config
from .provider_command_support import (
    _command_capability_failure,
    _command_capability_preflight,
    decode_provider_json,
)
from .providers.anysearch import AnySearchProvider


def _anysearch_provider() -> AnySearchProvider:
    """Build the uncached AnySearch command provider from current config."""
    return AnySearchProvider(config.anysearch_api_url, config.anysearch_api_key, config.anysearch_timeout)


async def anysearch_domains(domain: str = "") -> dict[str, Any]:
    """List AnySearch vertical domains without entering the shared cache."""
    start = time.time()
    preflight = _command_capability_preflight("anysearch-domains")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"domain": domain})
    result = await decode_provider_json(
        await _anysearch_provider().list_domains(domain),
        provider="anysearch",
        capability="vertical_search",
    )
    result.update(preflight.get("metadata") or {})
    return result


async def anysearch_search(
    query: str,
    domain: str = "",
    sub_domain: str = "",
    max_results: int = 5,
) -> dict[str, Any]:
    """Run one AnySearch vertical query."""
    start = time.time()
    preflight = _command_capability_preflight("anysearch-search")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"query": query})
    result = await decode_provider_json(
        await _anysearch_provider().vertical_search(
            query=query,
            domain=domain,
            sub_domain=sub_domain,
            max_results=max_results,
        ),
        provider="anysearch",
        capability="vertical_search",
    )
    result.update(preflight.get("metadata") or {})
    return result


async def anysearch_extract(url: str, max_length: int = 20000) -> dict[str, Any]:
    """Extract one URL through the explicit AnySearch vertical command."""
    start = time.time()
    preflight = _command_capability_preflight("anysearch-extract")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"url": url})
    result = await decode_provider_json(
        await _anysearch_provider().extract(url, max_length=max_length),
        provider="anysearch",
        capability="vertical_search",
    )
    result.update(preflight.get("metadata") or {})
    return result


async def anysearch_batch(queries: list[str], max_results: int = 3) -> dict[str, Any]:
    """Run an explicit AnySearch batch command."""
    start = time.time()
    preflight = _command_capability_preflight("anysearch-batch")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"queries": queries})
    result = await decode_provider_json(
        await _anysearch_provider().batch_search(queries, max_results=max_results),
        provider="anysearch",
        capability="vertical_search",
    )
    result.update(preflight.get("metadata") or {})
    return result


__all__ = ["anysearch_batch", "anysearch_domains", "anysearch_extract", "anysearch_search"]
