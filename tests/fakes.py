"""Shared fake HTTP transports for role-specific provider contract tests."""

from __future__ import annotations

from contextlib import asynccontextmanager


class FakeStream:
    def __init__(self, response=None, exception=None):
        self.response = response
        self.exception = exception

    async def __aenter__(self):
        if self.exception is not None:
            raise self.exception
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return None


class FakeTransport:
    def __init__(self, response=None, exception=None):
        self.response = response
        self.exception = exception
        self.calls = []

    def _record(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.exception is not None:
            raise self.exception
        return self.response

    async def get(self, url, headers=None, params=None, **kwargs):
        return self._record("GET", url, headers=headers or {}, params=params or {}, kwargs=kwargs)

    async def post(self, url, headers=None, json=None, **kwargs):
        return self._record("POST", url, headers=headers or {}, json=json, kwargs=kwargs)

    def stream(self, method, url, headers=None, json=None, **kwargs):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "json": json, "kwargs": kwargs})
        return FakeStream(self.response, self.exception)


def install_request_client(monkeypatch, module: str, transport: FakeTransport) -> FakeTransport:
    @asynccontextmanager
    async def fake_request_client(*args, **kwargs):
        yield transport

    monkeypatch.setattr(f"{module}.request_client", fake_request_client)
    return transport


def disable_retries(monkeypatch) -> None:
    monkeypatch.setenv("SMART_SEARCH_RETRY_MAX_ATTEMPTS", "0")
    monkeypatch.setenv("SMART_SEARCH_RETRY_MULTIPLIER", "0")
