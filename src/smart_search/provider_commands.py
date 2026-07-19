"""Compatibility facade for capability-owned provider commands."""

# Keep the shared httpx module reachable for legacy diagnostics tests. The
# command implementations themselves live in the owning modules below.
import httpx

from .providers.anysearch import AnySearchProvider
from .providers.exa import ExaSearchProvider
from .providers.jina import JinaReaderProvider
from .providers.zhipu import ZhipuWebSearchProvider
from .providers.zhipu_mcp import ZhipuMCPProvider
from .provider_command_support import decode_provider_json as _decode_provider_json
from .provider_fetch_commands import (
    call_firecrawl_scrape,
    call_jina_reader,
    call_tavily_extract,
    call_tavily_map,
    fetch,
    jina_fetch,
    map_site,
)
from .provider_mcp_commands import (
    zhipu_mcp_read_file,
    zhipu_mcp_reader,
    zhipu_mcp_repo_structure,
    zhipu_mcp_search,
    zhipu_mcp_search_doc,
)
from .provider_search_commands import (
    call_firecrawl_search,
    call_tavily_search,
    context7_docs,
    context7_library,
    exa_find_similar,
    exa_search,
    zhipu_search,
)
from .provider_vertical_commands import (
    anysearch_batch,
    anysearch_domains,
    anysearch_extract,
    anysearch_search,
)


__all__ = [
    "anysearch_batch",
    "anysearch_domains",
    "anysearch_extract",
    "anysearch_search",
    "call_firecrawl_scrape",
    "call_firecrawl_search",
    "call_jina_reader",
    "call_tavily_extract",
    "call_tavily_map",
    "call_tavily_search",
    "context7_docs",
    "context7_library",
    "exa_find_similar",
    "exa_search",
    "fetch",
    "jina_fetch",
    "map_site",
    "zhipu_mcp_read_file",
    "zhipu_mcp_reader",
    "zhipu_mcp_repo_structure",
    "zhipu_mcp_search",
    "zhipu_mcp_search_doc",
    "zhipu_search",
]
