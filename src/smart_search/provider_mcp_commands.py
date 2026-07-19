"""Zhipu Coding Plan MCP and zread command ownership."""

import time
from typing import Any

from .config import config
from .provider_command_support import (
    _command_capability_failure,
    _command_capability_preflight,
    decode_provider_json,
)
from .providers.zhipu_mcp import ZhipuMCPProvider


def _zhipu_mcp_provider(*, route: str, provider_id: str) -> ZhipuMCPProvider:
    """Build one explicit Zhipu Coding Plan MCP route."""
    urls = {
        "search": config.zhipu_mcp_search_api_url,
        "reader": config.zhipu_mcp_reader_api_url,
        "zread": config.zhipu_mcp_zread_api_url,
    }
    return ZhipuMCPProvider(
        urls[route],
        config.zhipu_mcp_api_key or "",
        config.zhipu_mcp_timeout,
        provider_id=provider_id,
    )


async def zhipu_mcp_search(query: str, count: int = 5) -> dict[str, Any]:
    """Run the explicit Zhipu MCP web_search route."""
    start = time.time()
    preflight = _command_capability_preflight("zhipu-mcp-search")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"query": query})
    result = await decode_provider_json(
        await _zhipu_mcp_provider(route="search", provider_id="zhipu-mcp").web_search(query, count=count),
        provider="zhipu-mcp",
        capability="web_search",
    )
    result.update(preflight.get("metadata") or {})
    return result


async def zhipu_mcp_reader(url: str) -> dict[str, Any]:
    """Run the explicit Zhipu MCP webReader route."""
    start = time.time()
    preflight = _command_capability_preflight("zhipu-mcp-reader")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"url": url})
    result = await decode_provider_json(
        await _zhipu_mcp_provider(route="reader", provider_id="zhipu-mcp-reader").web_reader(url),
        provider="zhipu-mcp-reader",
        capability="web_fetch",
    )
    result.update(preflight.get("metadata") or {})
    return result


async def zhipu_mcp_search_doc(repo: str, query: str, max_results: int = 5) -> dict[str, Any]:
    """Search repository documentation through the explicit zread capability."""
    start = time.time()
    preflight = _command_capability_preflight("zhipu-mcp-search-doc")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"repo": repo, "query": query})
    result = await decode_provider_json(
        await _zhipu_mcp_provider(route="zread", provider_id="zhipu-mcp-zread").search_doc(repo, query, max_results=max_results),
        provider="zhipu-mcp-zread",
        capability="zread",
    )
    result.update(preflight.get("metadata") or {})
    return result


async def zhipu_mcp_repo_structure(repo: str, ref: str = "") -> dict[str, Any]:
    """Read repository structure through the explicit zread capability."""
    start = time.time()
    preflight = _command_capability_preflight("zhipu-mcp-repo-structure")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"repo": repo, "ref": ref})
    result = await decode_provider_json(
        await _zhipu_mcp_provider(route="zread", provider_id="zhipu-mcp-zread").get_repo_structure(repo, ref=ref),
        provider="zhipu-mcp-zread",
        capability="zread",
    )
    result.update(preflight.get("metadata") or {})
    return result


async def zhipu_mcp_read_file(repo: str, path: str, ref: str = "") -> dict[str, Any]:
    """Read one repository file through the explicit zread capability."""
    start = time.time()
    preflight = _command_capability_preflight("zhipu-mcp-read-file")
    if not preflight.get("ok"):
        return _command_capability_failure(preflight, start, extra={"repo": repo, "path": path, "ref": ref})
    result = await decode_provider_json(
        await _zhipu_mcp_provider(route="zread", provider_id="zhipu-mcp-zread").read_file(repo, path, ref=ref),
        provider="zhipu-mcp-zread",
        capability="zread",
    )
    result.update(preflight.get("metadata") or {})
    return result


__all__ = [
    "zhipu_mcp_read_file",
    "zhipu_mcp_reader",
    "zhipu_mcp_repo_structure",
    "zhipu_mcp_search",
    "zhipu_mcp_search_doc",
]
