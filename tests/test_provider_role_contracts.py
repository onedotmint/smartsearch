"""Role-specific provider contracts through fake transports only."""

from __future__ import annotations

import json

import httpx
import pytest

from fakes import FakeTransport, disable_retries, install_request_client
from smart_search.providers.brave import BraveSearchProvider
from smart_search.providers.exa import ExaSearchProvider
from smart_search.providers.exa_reader import ExaReaderProvider
from smart_search.providers.firecrawl import FirecrawlReaderProvider
from smart_search.providers.jina import JinaReaderProvider
from smart_search.providers.jina_rerank import rerank as jina_rerank
from smart_search.providers.tavily import TavilySearchProvider

pytestmark = pytest.mark.contract

SECRET = "contract-secret-value"


@pytest.fixture(autouse=True)
def _disable_retries(monkeypatch):
    disable_retries(monkeypatch)


SEARCH_PROVIDERS = (
    pytest.param(
        "brave",
        lambda: BraveSearchProvider("https://api.search.brave.com/res/v1", SECRET),
        "smart_search.providers.brave",
        {"web": {"results": [{"title": "Result", "url": "https://example.com/1"}]}},
        id="brave",
    ),
    pytest.param(
        "exa",
        lambda: ExaSearchProvider("https://api.exa.ai", SECRET),
        "smart_search.providers.exa",
        {"results": [{"title": "Result", "url": "https://example.com/1"}]},
        id="exa",
    ),
    pytest.param(
        "tavily",
        lambda: TavilySearchProvider("https://api.tavily.com", SECRET),
        "smart_search.providers.tavily",
        {"results": [{"title": "Result", "url": "https://example.com/1", "content": "c"}]},
        id="tavily",
    ),
)

FETCH_PROVIDERS = (
    pytest.param(
        "jina",
        lambda: JinaReaderProvider("https://r.jina.ai", SECRET),
        "smart_search.providers.jina",
        httpx.Response(200, text="Title: Example\n\nBody.", request=httpx.Request("GET", "https://r.jina.ai/x")),
        "fetch",
        id="jina",
    ),
    pytest.param(
        "exa",
        lambda: ExaReaderProvider("https://api.exa.ai", SECRET),
        "smart_search.providers.exa",
        httpx.Response(
            200,
            json={"results": [{"title": "Page", "text": "Body from exa"}]},
            request=httpx.Request("POST", "https://api.exa.ai/contents"),
        ),
        "read",
        id="exa-reader",
    ),
    pytest.param(
        "firecrawl",
        lambda: FirecrawlReaderProvider("https://api.firecrawl.dev/v2", SECRET),
        "smart_search.providers.firecrawl",
        httpx.Response(
            200,
            json={"data": {"markdown": "Body from firecrawl"}},
            request=httpx.Request("POST", "https://api.firecrawl.dev/v2/scrape"),
        ),
        "read",
        id="firecrawl",
    ),
)


def _http_error(status_code: int, url: str = "https://example.invalid/search"):
    request = httpx.Request("GET", url)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=request,
        response=httpx.Response(status_code, text=f"HTTP {status_code} {SECRET}", request=request),
    )


def _json_response(payload, url: str = "https://example.invalid/search"):
    return httpx.Response(200, json=payload, request=httpx.Request("GET", url))


def _payload(result) -> dict:
    return result.to_dict() if hasattr(result, "to_dict") else json.loads(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id, factory, module, success_payload", SEARCH_PROVIDERS)
async def test_search_contract_success_identity_and_secret_containment(
    monkeypatch, provider_id, factory, module, success_payload
):
    transport = install_request_client(monkeypatch, module, FakeTransport(response=_json_response(success_payload)))
    result = await factory().search("query", num_results=3)
    payload = _payload(result)
    assert result.ok is True
    assert payload["provider"] == provider_id
    assert payload["results"]
    assert payload["results"][0]["url"].startswith("http")
    assert SECRET not in json.dumps(payload)
    assert transport.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id, factory, module, success_payload", SEARCH_PROVIDERS)
@pytest.mark.parametrize(("status_code", "error_type"), [(401, "auth_error"), (429, "rate_limited")])
async def test_search_contract_classifies_auth_and_rate_limit(
    monkeypatch, provider_id, factory, module, success_payload, status_code, error_type
):
    install_request_client(monkeypatch, module, FakeTransport(exception=_http_error(status_code)))
    result = await factory().search("query")
    payload = _payload(result)
    assert result.ok is False
    assert payload["error_type"] == error_type
    assert SECRET not in json.dumps(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id, factory, module, success_payload", SEARCH_PROVIDERS)
async def test_search_contract_timeout_and_malformed_json(monkeypatch, provider_id, factory, module, success_payload):
    install_request_client(
        monkeypatch,
        module,
        FakeTransport(exception=httpx.ReadTimeout(f"slow {SECRET}", request=httpx.Request("GET", "https://example.invalid"))),
    )
    timeout = await factory().search("query")
    assert timeout.ok is False
    assert _payload(timeout)["error_type"] == "timeout"
    assert SECRET not in json.dumps(_payload(timeout))

    install_request_client(
        monkeypatch,
        module,
        FakeTransport(response=httpx.Response(200, text="not-json", request=httpx.Request("GET", "https://example.invalid"))),
    )
    malformed = await factory().search("query")
    assert malformed.ok is False
    assert _payload(malformed)["error_type"] == "parse_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id, factory, module, response, method", FETCH_PROVIDERS)
async def test_fetch_contract_success_identity_and_secret_containment(
    monkeypatch, provider_id, factory, module, response, method
):
    transport = install_request_client(monkeypatch, module, FakeTransport(response=response))
    result = await getattr(factory(), method)("https://example.com/page")
    payload = _payload(result)
    assert result.ok is True
    assert payload["provider"] == provider_id
    content = payload.get("content") or ""
    assert content.strip()
    assert SECRET not in json.dumps(payload)
    assert transport.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id, factory, module, response, method", FETCH_PROVIDERS)
async def test_fetch_contract_auth_timeout_and_empty(monkeypatch, provider_id, factory, module, response, method):
    install_request_client(monkeypatch, module, FakeTransport(exception=_http_error(401)))
    auth = await getattr(factory(), method)("https://example.com/page")
    assert auth.ok is False
    assert _payload(auth)["error_type"] == "auth_error"
    assert SECRET not in json.dumps(_payload(auth))

    install_request_client(
        monkeypatch,
        module,
        FakeTransport(exception=httpx.TimeoutException(f"slow {SECRET}")),
    )
    timed_out = await getattr(factory(), method)("https://example.com/page")
    assert timed_out.ok is False
    assert _payload(timed_out)["error_type"] == "timeout"
    assert SECRET not in json.dumps(_payload(timed_out))

    empty_responses = {
        "jina": httpx.Response(200, text="  ", request=httpx.Request("GET", "https://r.jina.ai/x")),
        "exa": httpx.Response(
            200,
            json={"results": [{"text": "  "}]},
            request=httpx.Request("POST", "https://api.exa.ai/contents"),
        ),
        "firecrawl": httpx.Response(
            200,
            json={"data": {"markdown": ""}},
            request=httpx.Request("POST", "https://api.firecrawl.dev/v2/scrape"),
        ),
    }
    install_request_client(monkeypatch, module, FakeTransport(response=empty_responses[provider_id]))
    unused = await getattr(factory(), method)("https://example.com/page")
    assert unused.ok is False
    assert _payload(unused)["error_type"] in {"empty", "quality_error"}


@pytest.mark.asyncio
async def test_rerank_contract_missing_key_skips_transport(monkeypatch):
    transport = install_request_client(monkeypatch, "smart_search.providers.jina_rerank", FakeTransport())
    result = await jina_rerank("query", ["a", "b"])
    payload = _payload(result)
    assert result.ok is False
    assert payload["provider"] == "jina"
    assert payload["capability"] == "rerank"
    assert payload["error_type"] == "config_error"
    assert transport.calls == []


@pytest.mark.asyncio
async def test_rerank_contract_success_malformed_and_failure(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", SECRET)
    transport = install_request_client(
        monkeypatch,
        "smart_search.providers.jina_rerank",
        FakeTransport(
            response=httpx.Response(
                200,
                json={"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]},
                request=httpx.Request("POST", "https://api.jina.ai/v1/rerank"),
            )
        ),
    )
    success = await jina_rerank("query", ["first", "second"], top_n=2)
    payload = _payload(success)
    assert success.ok is True
    assert payload["provider"] == "jina"
    assert payload["results"][0]["index"] == 1
    assert SECRET not in json.dumps(payload)
    sent = transport.calls[0]
    assert sent["json"]["query"] == "query"
    assert sent["json"]["documents"] == ["first", "second"]
    assert SECRET in sent["headers"]["authorization"]

    install_request_client(
        monkeypatch,
        "smart_search.providers.jina_rerank",
        FakeTransport(response=httpx.Response(200, json={"results": "nope"}, request=httpx.Request("POST", "https://example.invalid"))),
    )
    malformed = await jina_rerank("query", ["a"])
    assert malformed.ok is False
    assert _payload(malformed)["error_type"] == "parse_error"

    install_request_client(
        monkeypatch,
        "smart_search.providers.jina_rerank",
        FakeTransport(exception=_http_error(500)),
    )
    failed = await jina_rerank("query", ["a"])
    assert failed.ok is False
    assert _payload(failed)["error_type"] == "network_error"
    assert SECRET not in json.dumps(_payload(failed))
