"""Direct v1 provider-role registry.

The registry is deliberately smaller than the removed capability/control layer:
providers are looked up by role and deterministic id, then called directly.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Protocol


class SearchProvider(Protocol):
    provider_id: str
    async def search(self, query: str, limit: int = 5) -> Any: ...


class ReaderProvider(Protocol):
    provider_id: str
    async def read(self, url: str) -> Any: ...


class Reranker(Protocol):
    provider_id: str
    async def rerank(self, query: str, documents: list[str], top_n: int = 5) -> Any: ...


@dataclass(frozen=True)
class ProviderAttempt:
    provider: str
    role: str
    status: str
    error_type: str = ""
    error: str = ""
    result_count: int = 0
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        result = {
            "provider": self.provider, "role": self.role, "status": self.status,
            "result_count": self.result_count, "elapsed_ms": self.elapsed_ms,
        }
        if self.error_type:
            result["error_type"] = self.error_type
        if self.error:
            result["error"] = self.error
        return result


class Registry:
    """Ordered role registry. Re-registering an id replaces it in place."""

    def __init__(self, *, search: Any = (), readers: Any = (), rerankers: Any = (),
                 search_providers: Any = None, reader_providers: Any = None) -> None:
        self._search: dict[str, Any] = {}
        self._readers: dict[str, Any] = {}
        self._rerankers: dict[str, Any] = {}
        search = search if search_providers is None else search_providers
        readers = readers if reader_providers is None else reader_providers
        for provider in search or ():
            self.register_search(provider)
        for provider in readers or ():
            self.register_reader(provider)
        for provider in rerankers or ():
            self.register_reranker(provider)

    @staticmethod
    def _id(provider: Any) -> str:
        value = getattr(provider, "provider_id", None) or getattr(provider, "id", None)
        if not value:
            raise ValueError("provider must define provider_id")
        return str(value).strip().lower()

    def register_search(self, provider: Any) -> Any:
        self._search[self._id(provider)] = provider
        return provider

    def register_reader(self, provider: Any) -> Any:
        self._readers[self._id(provider)] = provider
        return provider

    def register_reranker(self, provider: Any) -> Any:
        self._rerankers[self._id(provider)] = provider
        return provider

    def search_provider(self, provider_id: str) -> Any | None:
        return self._search.get(str(provider_id).strip().lower())

    def reader_provider(self, provider_id: str) -> Any | None:
        return self._readers.get(str(provider_id).strip().lower())

    def reranker(self, provider_id: str = "jina") -> Any | None:
        return self._rerankers.get(str(provider_id).strip().lower())

    get_search = search_provider
    get_reader = reader_provider
    get_reranker = reranker

    def search_providers(self, ids: Any = None) -> list[Any]:
        return self._ordered(self._search, ids)

    def reader_providers(self, ids: Any = None) -> list[Any]:
        return self._ordered(self._readers, ids)

    def rerankers(self) -> list[Any]:
        return list(self._rerankers.values())

    @staticmethod
    def _ordered(values: dict[str, Any], ids: Any) -> list[Any]:
        if ids is None:
            return list(values.values())
        return [values[str(item).strip().lower()] for item in ids if str(item).strip().lower() in values]

    @property
    def search_ids(self) -> tuple[str, ...]:
        return tuple(self._search)

    @property
    def reader_ids(self) -> tuple[str, ...]:
        return tuple(self._readers)

    def eligibility(self) -> dict[str, tuple[str, ...]]:
        return {"search": self.search_ids, "read": self.reader_ids, "rerank": tuple(self._rerankers)}


def _callable_provider(provider_id: str, role: str, fn: Callable[..., Any]) -> Any:
    async def call(*args: Any, **kwargs: Any) -> Any:
        result = fn(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result
    return type(f"{provider_id.title().replace('-', '')}{role.title()}Adapter", (), {
        "provider_id": provider_id,
        role: staticmethod(call),
    })()


def default_registry() -> Registry:
    """Construct configured direct adapters lazily, without network I/O."""
    from ..config import config

    search: list[Any] = []
    if config.brave_api_key and config.brave_enabled:
        try:
            from .brave import BraveSearchProvider
            search.append(BraveSearchProvider(config.brave_api_url, config.brave_api_key, config.brave_timeout))
        except ModuleNotFoundError:
            pass
    if config.exa_api_key:
        try:
            from .exa import ExaSearchProvider
            search.append(ExaSearchProvider(config.exa_base_url, config.exa_api_key, config.exa_timeout))
        except ModuleNotFoundError:
            pass

    readers: list[Any] = []
    # Jina is intentionally eligible anonymously; the adapter itself handles
    # the optional key/respond-with contract.
    if config.jina_reader_api_url:
        try:
            from .jina import JinaReaderProvider
            readers.append(JinaReaderProvider(config.jina_reader_api_url, config.jina_api_key,
                                              config.jina_respond_with, config.jina_timeout))
        except ModuleNotFoundError:
            pass
    if config.firecrawl_api_key:
        try:
            from .firecrawl import FirecrawlReaderProvider
            readers.append(FirecrawlReaderProvider(config.firecrawl_api_url, config.firecrawl_api_key, 90.0))
        except ModuleNotFoundError:
            pass
    if config.exa_api_key:
        try:
            from .exa_reader import ExaReaderProvider
            readers.append(ExaReaderProvider(config.exa_base_url, config.exa_api_key, config.exa_timeout))
        except ModuleNotFoundError:
            pass

    reranker = None
    if config.jina_api_key:
        try:
            from .jina_rerank import rerank
            reranker = _callable_provider("jina", "rerank", rerank)
        except ModuleNotFoundError:
            pass
    return Registry(search=search, readers=readers, rerankers=([reranker] if reranker else []))


__all__ = ["ProviderAttempt", "ReaderProvider", "Reranker", "Registry", "SearchProvider", "default_registry"]
