"""Frozen negative fixture for the removed exact Provider and Experimental public surface.

Generated from the pre-removal legacy inventory artifact (the removal authority):
every spelling, alias, namespace path, and public export listed on rows owned by
``provider-experimental-surface-removal``. Removal tests must fail deterministically for
each entry and live reconciliation must prove none of them remains advertised.
"""

from __future__ import annotations


# 14 exact provider parser leaves (kind=exact_provider_command).
REMOVED_EXACT_PROVIDER_COMMANDS: tuple[str, ...] = (
    'anysearch-batch',
    'anysearch-domains',
    'anysearch-extract',
    'anysearch-search',
    'context7-docs',
    'context7-library',
    'exa-search',
    'exa-similar',
    'zhipu-mcp-read-file',
    'zhipu-mcp-reader',
    'zhipu-mcp-repo-structure',
    'zhipu-mcp-search',
    'zhipu-mcp-search-doc',
    'zhipu-search',
)

# 20 top-level aliases of removed provider commands (kind=command_alias).
REMOVED_ALIASES: tuple[str, ...] = (
    'as',
    'as-batch',
    'as-domains',
    'as-extract',
    'as-search',
    'c7',
    'c7d',
    'c7docs',
    'ctx7',
    'ctx7-docs',
    'exa',
    'x',
    'xs',
    'z',
    'zmcp-doc',
    'zmcp-file',
    'zmcp-reader',
    'zmcp-search',
    'zmcp-tree',
    'zp',
)

# 14 removed namespace leaves (kind=namespace_leaf): path -> legacy command target.
REMOVED_NAMESPACE_PATHS: dict[str, str] = {
    'experimental anysearch batch': 'removed experimental vertical_search public surface',
    'experimental anysearch domains': 'removed experimental vertical_search public surface',
    'experimental anysearch extract': 'removed experimental vertical_search public surface',
    'experimental anysearch search': 'removed experimental vertical_search public surface',
    'experimental zread read-file': 'removed experimental zread public surface',
    'experimental zread repo-structure': 'removed experimental zread public surface',
    'experimental zread search-doc': 'removed experimental zread public surface',
    'provider context7 docs': 'canonical V2 Evidence command domain; provider selection is ',
    'provider context7 library': 'search (V2 docs_discovery)',
    'provider exa search': 'search (V2 source/docs discovery)',
    'provider exa similar': 'search (V2 source discovery; no brand similar leaf)',
    'provider zhipu search': 'search (V2 source_discovery)',
    'provider zhipu-mcp reader': 'fetch (V2 content_fetch)',
    'provider zhipu-mcp search': 'search (V2 source_discovery)',
}

# 20 removed public service facade exports (kind=python_export).
REMOVED_SERVICE_EXPORTS: tuple[str, ...] = (
    'anysearch_batch',
    'anysearch_domains',
    'anysearch_extract',
    'anysearch_search',
    'call_firecrawl_scrape',
    'call_firecrawl_search',
    'call_jina_reader',
    'call_tavily_extract',
    'call_tavily_map',
    'call_tavily_search',
    'context7_docs',
    'context7_library',
    'exa_find_similar',
    'exa_search',
    'zhipu_mcp_read_file',
    'zhipu_mcp_reader',
    'zhipu_mcp_repo_structure',
    'zhipu_mcp_search',
    'zhipu_mcp_search_doc',
    'zhipu_search',
)

# Namespace argv forms that must fail at the provider/experimental subparser level.
REMOVED_NAMESPACE_ARGV: tuple[tuple[str, ...], ...] = (
    ('experimental', 'anysearch', 'batch', 'a', 'b'),
    ('experimental', 'anysearch', 'domains'),
    ('experimental', 'anysearch', 'extract', 'https://example.com'),
    ('experimental', 'anysearch', 'search', 'query'),
    ('experimental', 'zread', 'read-file', 'owner/repo', 'README.md'),
    ('experimental', 'zread', 'repo-structure', 'owner/repo'),
    ('experimental', 'zread', 'search-doc', 'owner/repo', 'query'),
    ('provider', 'context7', 'docs', '/react', 'hooks'),
    ('provider', 'context7', 'library', 'react'),
    ('provider', 'exa', 'search', 'query'),
    ('provider', 'exa', 'similar', 'https://example.com'),
    ('provider', 'zhipu', 'search', 'query'),
    ('provider', 'zhipu-mcp', 'reader', 'https://example.com'),
    ('provider', 'zhipu-mcp', 'search', 'query'),
)
