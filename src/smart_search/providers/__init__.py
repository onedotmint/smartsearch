"""Provider transports and the v1 role adapters.

Concrete transports are loaded lazily so parser/help/error paths do not import
optional HTTP dependencies or read configuration.
"""
from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "BaseSearchProvider": ("base", "BaseSearchProvider"),
    "ProviderError": ("base", "ProviderError"),
    "ProviderResult": ("base", "ProviderResult"),
    "ProviderTimeoutError": ("base", "ProviderTimeoutError"),
    "SearchResult": ("base", "SearchResult"),
    "classify_provider_exception": ("base", "classify_provider_exception"),
    "coerce_provider_result": ("base", "coerce_provider_result"),
    "BraveSearchProvider": ("brave", "BraveSearchProvider"),
    "ExaSearchProvider": ("exa", "ExaSearchProvider"),
    "ExaReaderProvider": ("exa_reader", "ExaReaderProvider"),
    "Context7Provider": ("context7", "Context7Provider"),
    "AnySearchProvider": ("anysearch", "AnySearchProvider"),
    "ZhipuWebSearchProvider": ("zhipu", "ZhipuWebSearchProvider"),
    "ZhipuMCPProvider": ("zhipu_mcp", "ZhipuMCPProvider"),
    "XAIResponsesSearchProvider": ("xai_responses", "XAIResponsesSearchProvider"),
    "OpenAICompatibleSearchProvider": ("openai_compatible", "OpenAICompatibleSearchProvider"),
    "JinaReaderProvider": ("jina", "JinaReaderProvider"),
    "FirecrawlReaderProvider": ("firecrawl", "FirecrawlReaderProvider"),
}


def __getattr__(name: str):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value


__all__ = list(_EXPORTS)
