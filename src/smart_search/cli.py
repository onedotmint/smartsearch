"""Single stable v1 JSON CLI for search, read, and research."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import is_dataclass
from typing import Any
from collections.abc import Mapping, Iterable

from .core.models import Evidence, RetrievalPolicy, ResearchRun, thaw
from .core.retrieval import RetrievalOutcome, search as core_search
from .evidence.fetch import FetchOutcome, read as core_read
from .security import safe_provider_message, sanitize_data
from .research.runner import run as research_run

EXIT_OK = 0
EXIT_INVALID_ARGUMENT = 2
EXIT_CONFIGURATION = 3
EXIT_PROVIDER = 4
EXIT_INTERNAL = 5


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


_KNOWN_ATTEMPT_ERROR_TYPES = frozenset({
    "config_error", "auth_error", "parameter_error", "timeout", "network_error",
    "rate_limited", "protocol_error", "parse_error", "quality_error", "empty",
    "provider_error", "budget_exhausted", "too_large",
})
_STABLE_ATTEMPT_FIELDS = ("provider", "role", "status", "result_count", "elapsed_ms")


def _safe_attempt(item: Any) -> dict[str, Any]:
    """Serialize only stable attempt fields and classifications."""
    value = item.to_dict() if hasattr(item, "to_dict") else item
    value = thaw(value)
    if not isinstance(value, Mapping):
        return {
            "provider": "provider", "role": "unknown", "status": "failed",
            "result_count": 0, "elapsed_ms": 0.0,
            "error_type": "protocol_error",
            "error": safe_provider_message("protocol_error"),
        }
    result = {key: value[key] for key in _STABLE_ATTEMPT_FIELDS if key in value}
    raw_type = value.get("error_type", "")
    status = result.get("status", "")
    if raw_type:
        error_type = raw_type if isinstance(raw_type, str) and raw_type in _KNOWN_ATTEMPT_ERROR_TYPES else "protocol_error"
    elif status in {"failed", "error"}:
        error_type = "provider_error"
    else:
        error_type = ""
    if error_type:
        result["error_type"] = error_type
        result["error"] = safe_provider_message(error_type)
    return result


def _envelope(
    operation: str,
    status: str,
    data: Any = None,
    *,
    attempts=(),
    warnings=(),
    error=None,
    secrets: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "version": 1,
        "operation": operation,
        "status": status,
        "data": sanitize_data(data if data is not None else {}, secrets),
        "attempts": sanitize_data([_safe_attempt(item) for item in attempts], secrets),
        "warnings": sanitize_data([str(item) for item in warnings], secrets),
        "error": sanitize_data(error, secrets),
    }


def _candidate(item: Any) -> dict[str, Any]:
    candidate = item.candidate
    return {
        "url": candidate.url,
        "display_url": candidate.display_url,
        "title": candidate.title,
        "snippet": candidate.snippet,
        "providers": list(candidate.providers),
        "provider_ranks": dict(candidate.provider_ranks),
        "rrf_score": item.rrf_score,
        "rank": item.rank,
    }


def _evidence(item: Evidence) -> dict[str, Any]:
    return {
        "id": item.id,
        "url": item.url,
        "title": item.title,
        "provider": item.provider,
        "content": item.content,
        "truncated": item.truncated,
        "original_length": item.original_length,
        "returned_length": item.returned_length,
    }


def serialize(value: Any) -> Any:
    if isinstance(value, RetrievalOutcome):
        return {
            "candidates": [_candidate(item) for item in value.ranked],
            "providers": list(value.providers),
        }
    if isinstance(value, FetchOutcome):
        return {"evidence": _evidence(value.evidence)} if value.evidence else {"evidence": None}
    if isinstance(value, ResearchRun):
        data = value.to_dict()
        data["evidence"] = [_evidence(item) for item in value.evidence]
        data["candidates"] = [_candidate(item) for item in value.candidates]
        data["attempts"] = [_safe_attempt(item) for item in value.attempts]
        return data
    if is_dataclass(value):
        return thaw(value)
    return value


def _known_secrets() -> tuple[str, ...]:
    """Snapshot configured secret values only after command dispatch."""
    try:
        from .config import config
        values = config.snapshot.values
    except Exception:
        return ()

    secrets: list[str] = []
    def collect(value: Any, key: str = "") -> None:
        if isinstance(value, Mapping):
            for name, item in value.items():
                collect(item, str(name))
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item, key)
        elif value not in (None, "") and (key or "").lower().replace("-", "_").endswith(("_key", "_token", "_secret", "password")):
            secrets.append(str(value))
    collect(values)
    return tuple(dict.fromkeys(secrets))


def _safe_envelope(operation: str, status: str, data: Any = None, **kwargs: Any) -> dict[str, Any]:
    return _envelope(operation, status, data, secrets=_known_secrets(), **kwargs)


def _error(code: str, message: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {"code": code, "message": message}
    if details:
        result["details"] = details
    return result

def _error_code(attempts: Any, default: str = "PROVIDER_ERROR") -> str:
    types = set()
    for item in attempts:
        value = item.get("error_type") if isinstance(item, Mapping) else getattr(item, "error_type", "")
        if value:
            types.add(str(value))
    if "config_error" in types and types <= {"config_error", "empty"}:
        return "CONFIGURATION_ERROR"
    return default


def _status_for_search(outcome: RetrievalOutcome) -> str:
    return "degraded" if outcome.degraded else "complete"


def _status_for_read(outcome: FetchOutcome) -> str:
    if not outcome.evidence:
        return "failed"
    return "degraded" if outcome.degraded else "complete"


def _status_for_research(run: ResearchRun) -> str:
    if not run.evidence:
        return "failed"
    if run.gaps or any(
        isinstance(item, Mapping) and item.get("status") in {"failed", "error"}
        for item in run.attempts
    ):
        return "degraded"
    return "complete"
async def run_search(
    query: str,
    *,
    max_results: int = 5,
    rerank: bool = True,
    registry=None,
) -> dict[str, Any]:
    if not str(query or "").strip():
        return _safe_envelope("search", "failed", error=_error("INVALID_ARGUMENT", "query is required"))
    if not isinstance(max_results, int) or isinstance(max_results, bool) or max_results < 1:
        return _safe_envelope("search", "failed", error=_error("INVALID_ARGUMENT", "max_results must be positive"))
    outcome = await core_search(query, RetrievalPolicy(max_results=max_results, rerank=rerank), registry=registry)
    status = _status_for_search(outcome)
    error = None
    if outcome.failed:
        code = _error_code(outcome.attempts)
        error = _error(code, "no search provider returned usable results")
        status = "failed"
    return _safe_envelope("search", status, serialize(outcome), attempts=outcome.attempts, warnings=outcome.warnings, error=error)


async def run_read(url: str, *, max_chars: int = 8_000, registry=None) -> dict[str, Any]:
    try:
        outcome = await core_read(url, registry=registry, max_chars=max_chars)
    except ValueError as exc:
        return _safe_envelope("read", "failed", error=_error("INVALID_ARGUMENT", str(exc)))
    error = None
    if not outcome.evidence:
        code = _error_code(outcome.attempts)
        error = _error(code, "no reader returned usable evidence")
    return _safe_envelope("read", _status_for_read(outcome), serialize(outcome), attempts=outcome.attempts, warnings=outcome.warnings, error=error)


async def run_research(query: str, *, registry=None) -> dict[str, Any]:
    try:
        result = await research_run(
            query,
            search_fn=lambda value: core_search(value, registry=registry),
            read_fn=lambda value: core_read(value, registry=registry),
        )
    except ValueError as exc:
        return _safe_envelope("research", "failed", error=_error("INVALID_ARGUMENT", str(exc)))
    status = _status_for_research(result)
    error = None
    if status == "failed":
        error = _error(_error_code(result.attempts), "research did not produce a usable evidence run")
    return _safe_envelope(
        "research", status, serialize(result), attempts=result.attempts, error=error
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="smart-search", description="Evidence-first web search")
    sub = parser.add_subparsers(dest="operation", required=True, parser_class=_Parser)

    search_parser = sub.add_parser("search", help="search for sources")
    search_parser.add_argument("query")
    search_parser.add_argument("--max-results", type=int, default=5)
    search_parser.add_argument("--no-rerank", action="store_true")
    search_parser.add_argument("--format", choices=("json",), default="json", help=argparse.SUPPRESS)

    read_parser = sub.add_parser("read", help="read a URL")
    read_parser.add_argument("url")
    read_parser.add_argument("--max-chars", type=int, default=8_000)
    read_parser.add_argument("--format", choices=("json",), default="json", help=argparse.SUPPRESS)

    research_parser = sub.add_parser("research", help="collect evidence without writing an answer")
    research_parser.add_argument("query")
    research_parser.add_argument("--format", choices=("json",), default="json", help=argparse.SUPPRESS)
    return parser


HELP_ALL = """smart-search v1 commands:\n  search QUERY\n  read URL\n  research QUERY\n\nAll commands emit one version-1 JSON envelope.\n"""


def _requested_operation(argv: list[str]) -> str:
    for token in argv:
        if token == "--":
            break
        if token in {"search", "read", "research"}:
            return token
    return "unknown"

def _parse(argv: list[str]) -> argparse.Namespace:
    try:
        return build_parser().parse_args(argv)
    except SystemExit:
        raise
    except (argparse.ArgumentError, ValueError) as exc:
        raise ValueError(str(exc)) from None


def _exit_code(payload: dict[str, Any]) -> int:
    if payload["status"] != "failed":
        return EXIT_OK
    code = (payload.get("error") or {}).get("code")
    return {
        "INVALID_ARGUMENT": EXIT_INVALID_ARGUMENT,
        "CONFIGURATION_ERROR": EXIT_CONFIGURATION,
        "PROVIDER_ERROR": EXIT_PROVIDER,
        "INTERNAL_ERROR": EXIT_INTERNAL,
    }.get(code, EXIT_PROVIDER)


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw == ["--help-all"]:
        sys.stdout.write(HELP_ALL)
        return EXIT_OK
    try:
        args = _parse(raw)
    except SystemExit as exc:
        return int(exc.code or 0)
    except ValueError as exc:
        operation = _requested_operation(raw)
        message = str(exc)
        # Parser diagnostics must not echo arbitrary tokens (which may be
        # credentials); retain only stable, useful classifications.
        safe_message = "a required argument is missing" if "required" in message.lower() else "invalid command or arguments"
        payload = _envelope(operation, "failed", error=_error("INVALID_ARGUMENT", safe_message, details={"operation": operation}))
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return EXIT_INVALID_ARGUMENT

    try:
        if args.operation == "search":
            payload = asyncio.run(run_search(args.query, max_results=args.max_results, rerank=not args.no_rerank))
        elif args.operation == "read":
            payload = asyncio.run(run_read(args.url, max_chars=args.max_chars))
        else:
            payload = asyncio.run(run_research(args.query))
    except Exception:
        payload = _envelope(args.operation, "failed", error=_error("INTERNAL_ERROR", "operation failed"))
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return _exit_code(payload)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["EXIT_OK", "build_parser", "main", "run_read", "run_research", "run_search", "serialize"]
