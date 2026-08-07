"""AnySearch vertical provider command ownership."""

import time
from typing import Any

from .capability_service import _capability_preflight, _command_capability_failure
from .config import config
from .provider_command_support import decode_provider_json
from .providers.anysearch import AnySearchProvider


def _anysearch_provider() -> AnySearchProvider:
    """Build the uncached AnySearch command provider from current config."""
    return AnySearchProvider(config.anysearch_api_url, config.anysearch_api_key, config.anysearch_timeout)


async def anysearch_domains(domain: str = "") -> dict[str, Any]:
    """List AnySearch vertical domains without entering the shared cache."""
    start = time.time()
    preflight = _capability_preflight("vertical_search", provider="anysearch")
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
    preflight = _capability_preflight("vertical_search", provider="anysearch")
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


__all__ = ["anysearch_domains", "anysearch_search"]
