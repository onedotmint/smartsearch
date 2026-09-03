"""Validated, bounded ordered reader fallback."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Mapping, Sequence, TYPE_CHECKING
from urllib.parse import urlsplit

from ..core.models import Evidence
from ..security import safe_provider_message

if TYPE_CHECKING:
    from ..providers.registry import ProviderAttempt, Registry

DEFAULT_CONTENT_LIMIT = 8_000
_KNOWN_ERROR_TYPES = frozenset({
    "config_error", "auth_error", "parameter_error", "timeout", "network_error",
    "rate_limited", "protocol_error", "parse_error", "quality_error", "empty",
    "provider_error", "budget_exhausted", "too_large",
})


_CHALLENGE_MARKERS = (
    "title: just a moment", "checking if the site connection is secure",
    "attention required! | cloudflare", "enable javascript and cookies to continue",
)


def _stable_error_type(value: Any) -> str:
    """Keep provider-controlled error labels inside the stable error protocol."""
    if not value:
        return ""
    return value if isinstance(value, str) and value in _KNOWN_ERROR_TYPES else "protocol_error"


def _classify(exc: BaseException) -> tuple[str, str, bool]:
    try:
        from ..providers.base import classify_provider_exception
    except ModuleNotFoundError:
        return "provider_error", safe_provider_message("provider_error"), False
    return classify_provider_exception(exc)


@dataclass(frozen=True)
class FetchOutcome:
    evidence: Evidence | None
    attempts: tuple[ProviderAttempt, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempts", tuple(self.attempts))
        object.__setattr__(self, "warnings", tuple(str(item) for item in self.warnings))

    @property
    def ok(self) -> bool:
        return self.evidence is not None

    @property
    def degraded(self) -> bool:
        return self.evidence is not None and any(
            getattr(attempt, "status", "") not in {"complete", "success"}
            for attempt in self.attempts
        )


def validate_url(url: str) -> str:
    value = str(url or "").strip()
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        raise ValueError("url must be an absolute http(s) URL without credentials") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or any(character.isspace() for character in value)
        or parsed.username
        or parsed.password
    ):
        raise ValueError("url must be an absolute http(s) URL without credentials")
    return value


def _result(value: Any, provider_id: str) -> tuple[bool, str, str, str, str, str]:
    if isinstance(value, Evidence):
        if not value.url or not value.provider or not isinstance(value.content, str):
            return False, "", "", provider_id, "protocol_error", safe_provider_message("protocol_error")
        return bool(value.content.strip()), value.content, value.title, value.provider, "", ""
    data = value.data if isinstance(getattr(value, "data", None), Mapping) else value
    if isinstance(data, Mapping):
        raw_ok = getattr(value, "ok", data.get("ok", True))
        if type(raw_ok) is not bool:
            return False, "", "", provider_id, "protocol_error", safe_provider_message("protocol_error")
        content = data.get("content") if "content" in data else data.get("raw_content", "")
        if content is not None and not isinstance(content, str):
            return False, "", "", provider_id, "protocol_error", safe_provider_message("protocol_error")
        raw_error_type = getattr(value, "error_type", data.get("error_type", ""))
        return (
            raw_ok,
            content or "",
            str(data.get("title") or ""),
            provider_id,
            _stable_error_type(raw_error_type),
            str(getattr(value, "error", data.get("error", "")) or ""),
        )
    if isinstance(value, str):
        return bool(value.strip()), value, "", provider_id, "", ""
    return False, "", "", provider_id, "protocol_error", safe_provider_message("protocol_error")


async def _call(reader: Any, url: str) -> Any:
    method = getattr(reader, "read", None) or getattr(reader, "fetch", None)
    if method is None:
        raise TypeError("reader does not implement read or fetch")
    result = method(url)
    return await result if inspect.isawaitable(result) else result


async def read(
    url: str,
    *,
    registry: Registry | None = None,
    providers: Sequence[str] | None = None,
    max_chars: int = DEFAULT_CONTENT_LIMIT,
) -> FetchOutcome:
    """Try readers in registry order; empty and failed results remain visible."""
    from ..providers.registry import ProviderAttempt, default_registry
    target = validate_url(url)
    if not isinstance(max_chars, int) or max_chars < 1:
        raise ValueError("max_chars must be a positive integer")
    registry = registry or default_registry()
    readers = registry.reader_providers(providers)
    attempts: list[ProviderAttempt] = []
    warnings: list[str] = []
    for reader in readers:
        provider_id = str(getattr(reader, "provider_id", "reader"))
        try:
            value = await _call(reader, target)
            ok, content, title, provider, error_type, _error = _result(value, provider_id)
            if not ok:
                attempts.append(ProviderAttempt(
                    provider_id,
                    "read",
                    "failed" if error_type else "empty",
                    error_type or "empty",
                    safe_provider_message(error_type),
                ))
                continue
            if not content.strip():
                attempts.append(ProviderAttempt(provider_id, "read", "empty", "empty", "reader returned no content"))
                continue
            if any(marker in content.strip().lower() for marker in _CHALLENGE_MARKERS):
                attempts.append(ProviderAttempt(provider_id, "read", "failed", "quality_error", "challenge page detected"))
                continue
            original_length = len(content)
            bounded = content[:max_chars]
            evidence = Evidence(target, bounded, provider, title,
                                 truncated=original_length > max_chars,
                                 original_length=original_length,
                                 returned_length=len(bounded))
            attempts.append(ProviderAttempt(provider_id, "read", "complete", result_count=1))
            return FetchOutcome(evidence, tuple(attempts), tuple(warnings))
        except Exception as exc:
            error_type, _message, _retryable = _classify(exc)
            attempts.append(ProviderAttempt(provider_id, "read", "failed", error_type,
                                             safe_provider_message(error_type), 0))
    if readers:
        warnings.append("all readers failed or returned unusable content")
    else:
        warnings.append("no eligible reader providers")
    return FetchOutcome(None, tuple(attempts), tuple(warnings))


fetch = read
__all__ = ["DEFAULT_CONTENT_LIMIT", "FetchOutcome", "fetch", "read", "validate_url"]
