"""Tests for the shared typed execution primitives.

Covers valid/invalid value matrices, deep immutability, fresh projection
isolation, recursive redaction, finite-number checks, details collisions,
outcome payload isolation, strict typed-only projection and the forbidden-import
AST gate.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from smart_search.execution_primitives import (
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionCandidate,
    ExecutionCitation,
    ExecutionError,
    ExecutionEvidenceItem,
    ExecutionGap,
    ExecutionMetadata,
    ExecutionOutcome,
    budget_exhausted_attempt,
    empty_attempt,
    error_attempt,
    project_attempt_dict,
    project_attempts_dict,
    skipped_attempt,
    success_attempt,
)

MODULE_PATH = Path("src/smart_search/execution_primitives.py")


# ---------------------------------------------------------------------------
# ExecutionError
# ---------------------------------------------------------------------------


def test_error_requires_nonblank_type_and_message():
    with pytest.raises(ValueError):
        ExecutionError("", "boom")
    with pytest.raises(ValueError):
        ExecutionError("network_error", "  ")
    with pytest.raises(ValueError):
        ExecutionError("network_error", "boom", retryable="yes")


def test_error_frozen_details_immutable():
    err = ExecutionError("network_error", "boom", retryable=False, details={"cause": "x"})
    assert isinstance(err.details, dict) is False
    with pytest.raises(TypeError):
        err.details["cause"] = "y"  # type: ignore[index]


def test_error_rejects_non_json_details():
    with pytest.raises(ValueError):
        ExecutionError("network_error", "boom", details={"bad": object()})
    with pytest.raises(ValueError):
        ExecutionError("network_error", "boom", details={1: "x"})
    with pytest.raises(ValueError):
        ExecutionError("network_error", "boom", details={"bad": float("nan")})


# ---------------------------------------------------------------------------
# ExecutionAttempt
# ---------------------------------------------------------------------------


def test_attempt_invariants():
    ok = success_attempt("web_search", "tavily", elapsed_ms=1.0, result_count=1)
    assert ok.status is ExecutionAttemptStatus.OK
    assert ok.error is None
    assert ok.details == {}

    empty = empty_attempt("web_search", "zhipu", elapsed_ms=1.0)
    assert empty.status is ExecutionAttemptStatus.EMPTY
    assert empty.error is not None and empty.error.type == "empty"
    assert empty.result_count == 0

    err = error_attempt("web_search", "tavily", error_type="timeout", message="timed out", elapsed_ms=2.0)
    assert err.status is ExecutionAttemptStatus.ERROR
    assert err.error is not None and err.error.type == "timeout"
    assert err.error.retryable is None

    skipped = skipped_attempt(
        "web_search",
        "tavily",
        error_type="config_error",
        message="provider_not_eligible",
        elapsed_ms=1.0,
        details={"configured": True, "enabled": False, "eligible": False, "reason": "missing key"},
    )
    assert skipped.status is ExecutionAttemptStatus.SKIPPED
    assert skipped.error is not None and skipped.error.type == "config_error"
    assert skipped.error.retryable is False


def test_attempt_rejects_invalid_status_combinations():
    with pytest.raises(ValueError):
        ExecutionAttempt("web_search", "tavily", "ok", ExecutionError("timeout", "boom"))
    with pytest.raises(ValueError):
        ExecutionAttempt("web_search", "tavily", "empty", None)
    with pytest.raises(ValueError):
        ExecutionAttempt("web_search", "tavily", "empty", ExecutionError("timeout", "boom"))
    with pytest.raises(ValueError):
        ExecutionAttempt("web_search", "tavily", "error", None)
    with pytest.raises(ValueError):
        ExecutionAttempt("web_search", "tavily", "skipped", None)


def test_attempt_rejects_identity_and_numbers():
    with pytest.raises(ValueError):
        ExecutionAttempt("", "tavily", "ok")
    with pytest.raises(ValueError):
        ExecutionAttempt("web_search", "", "ok")
    with pytest.raises(ValueError):
        ExecutionAttempt("web_search", "tavily", "ok", None, elapsed_ms=-1.0)
    with pytest.raises(ValueError):
        ExecutionAttempt("web_search", "tavily", "ok", None, elapsed_ms=float("nan"))
    with pytest.raises(ValueError):
        ExecutionAttempt("web_search", "tavily", "ok", None, elapsed_ms=float("inf"))
    with pytest.raises(ValueError):
        ExecutionAttempt("web_search", "tavily", "ok", None, result_count=True)  # bool as counter
    with pytest.raises(ValueError):
        ExecutionAttempt("web_search", "tavily", "ok", None, result_count=-1)


def test_attempt_details_collision_rejected():
    with pytest.raises(ValueError):
        ExecutionAttempt(
            "web_search",
            "tavily",
            "ok",
            None,
            details={"status": "ok"},
        )
    with pytest.raises(ValueError):
        ExecutionAttempt(
            "web_search",
            "tavily",
            "ok",
            None,
            details={"error_type": "x"},
        )


def test_attempt_deep_immutability():
    attempt = success_attempt(
        "web_search",
        "tavily",
        elapsed_ms=1.0,
        result_count=1,
        details={"cache_hit": True},
    )
    assert isinstance(attempt.details, dict) is False
    with pytest.raises(TypeError):
        attempt.details["cache_hit"] = False  # type: ignore[index]
    assert attempt.details["cache_hit"] is True


def test_identity_fields_reject_whitespace_only():
    # Non-blank identity fields must reject whitespace-only strings, not just "".
    with pytest.raises(ValueError):
        ExecutionAttempt("   ", "tavily", "ok")
    with pytest.raises(ValueError):
        ExecutionAttempt("web_search", " \t ", "ok")
    with pytest.raises(ValueError):
        ExecutionError("network_error", " ")
    with pytest.raises(ValueError):
        ExecutionCandidate("  ", "https://example.com", "tavily", "title", "")


# ---------------------------------------------------------------------------
# Legacy projection
# ---------------------------------------------------------------------------


def test_project_ok_attempt():
    attempt = success_attempt("web_search", "tavily", elapsed_ms=2.5, result_count=3)
    data = project_attempt_dict(attempt)
    assert data == {
        "capability": "web_search",
        "provider": "tavily",
        "status": "ok",
        "error_type": "",
        "error": "",
        "elapsed_ms": 2.5,
        "result_count": 3,
    }
    assert "retryable" not in data


def test_project_empty_attempt_keeps_classified_error():
    attempt = empty_attempt("web_search", "zhipu", elapsed_ms=1.0)
    data = project_attempt_dict(attempt)
    assert data["status"] == "empty"
    assert data["error_type"] == "empty"
    assert data["error"] == "provider returned no usable result"
    assert data["retryable"] is False


def test_project_skipped_and_error_attempts():
    skipped = skipped_attempt(
        "web_search",
        "tavily",
        error_type="config_error",
        message="missing key",
        elapsed_ms=1.0,
        details={"configured": True, "enabled": False, "eligible": False, "reason": "missing key"},
    )
    data = project_attempt_dict(skipped)
    assert data["status"] == "skipped"
    assert data["error_type"] == "config_error"
    assert data["retryable"] is False
    assert data["configured"] is True
    assert data["enabled"] is False
    assert data["eligible"] is False
    assert data["reason"] == "missing key"

    err = error_attempt("web_search", "tavily", error_type="timeout", message="timed out", elapsed_ms=2.0)
    data = project_attempt_dict(err)
    assert data["status"] == "error"
    assert data["error_type"] == "timeout"
    assert "retryable" not in data


def test_project_budget_exhausted_attempt():
    attempt = budget_exhausted_attempt("web_fetch", elapsed_ms=1.0)
    data = project_attempt_dict(attempt)
    assert data["status"] == "skipped"
    assert data["provider"] == "request-budget"
    assert data["error_type"] == "budget_exhausted"
    assert data["retryable"] is False
    assert data["budget_exhausted"] is True


def test_project_returns_fresh_dict_each_call():
    attempt = success_attempt("web_search", "tavily", elapsed_ms=1.0, result_count=1)
    first = project_attempt_dict(attempt)
    second = project_attempt_dict(attempt)
    assert first is not second
    first["provider"] = "mutated"
    third = project_attempt_dict(attempt)
    assert third["provider"] == "tavily"


def test_project_recursive_redaction():
    attempt = error_attempt(
        "web_search",
        "tavily",
        error_type="network_error",
        message="Bearer sk-secret-123 failed",
        elapsed_ms=1.0,
        details={"reason": "https://user:pass@example.com?token=abc"},
    )
    data = project_attempt_dict(attempt, secrets=["sk-secret-123"])
    assert "sk-secret-123" not in str(data)
    assert "user:pass" not in str(data)
    assert "token=abc" not in str(data)
    assert "[REDACTED]" in str(data)


def test_project_attempts_dict_returns_fresh_lists():
    attempts = [success_attempt("web_search", "tavily", elapsed_ms=1.0, result_count=1)]
    out = project_attempts_dict(attempts)
    assert isinstance(out, list)
    assert isinstance(out[0], dict)
    out[0]["provider"] = "mutated"
    assert project_attempts_dict(attempts)[0]["provider"] == "tavily"


def test_project_attempts_dict_rejects_mapping_and_arbitrary_objects():
    with pytest.raises(TypeError):
        project_attempts_dict({"capability": "web_search"})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        project_attempts_dict(["not-an-attempt"])  # type: ignore[list-item]
    with pytest.raises(TypeError):
        project_attempts_dict(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Candidate / Evidence / Citation / Gap / Metadata
# ---------------------------------------------------------------------------


def test_candidate_evidence_citation_gap_metadata_valid():
    cand = ExecutionCandidate("c1", "https://example.com", "tavily", "Title", "snippet")
    assert cand.id == "c1"
    item = ExecutionEvidenceItem("e1", "https://example.com", "tavily", "Title", "body")
    assert item.content == "body"
    cit = ExecutionCitation("cite1", "e1", "ref")
    assert cit.evidence_id == "e1"
    gap = ExecutionGap("fetch_failed", "could not fetch", "web_fetch", "https://example.com")
    assert gap.code == "fetch_failed"
    meta = ExecutionMetadata("req-1", 12.5, ("warn",))
    assert meta.warnings == ("warn",)


def test_identity_validation_negative():
    with pytest.raises(ValueError):
        ExecutionCandidate("", "https://example.com", "tavily")
    with pytest.raises(ValueError):
        ExecutionEvidenceItem("e1", "", "tavily")
    with pytest.raises(ValueError):
        ExecutionCitation("", "e1", "ref")
    with pytest.raises(ValueError):
        ExecutionGap("", "message")
    with pytest.raises(ValueError):
        ExecutionMetadata("", 1.0)


def test_candidate_requires_title_or_snippet():
    with pytest.raises(ValueError):
        ExecutionCandidate("c1", "https://example.com", "tavily", "", "")
    with pytest.raises(ValueError):
        ExecutionCandidate("c1", "https://example.com", "tavily", "   ", "  ")
    # A whitespace-only title with a real snippet is acceptable.
    cand = ExecutionCandidate("c1", "https://example.com", "tavily", "  ", "snippet")
    assert cand.snippet == "snippet"


def test_evidence_requires_nonblank_content():
    with pytest.raises(ValueError):
        ExecutionEvidenceItem("e1", "https://example.com", "tavily", "Title", "")
    with pytest.raises(ValueError):
        ExecutionEvidenceItem("e1", "https://example.com", "tavily", "Title", "   ")


def test_citation_requires_nonblank_label():
    with pytest.raises(ValueError):
        ExecutionCitation("cite1", "e1", "")
    with pytest.raises(ValueError):
        ExecutionCitation("cite1", "e1", "   ")


def test_citation_requires_label_arg():
    # ``label`` is a required field; omitting it raises TypeError before any
    # nonblank validation runs, and an explicit blank value still raises ValueError.
    with pytest.raises(TypeError):
        ExecutionCitation("cite1", "e1")  # type: ignore[call-arg]


def test_metadata_requires_nonblank_request_id():
    # ``request_id`` is a required field; omitting it raises TypeError before any
    # nonblank validation runs, and an explicit blank value still raises ValueError.
    with pytest.raises(TypeError):
        ExecutionMetadata(duration_ms=1.0)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        ExecutionMetadata("   ", 1.0)
    with pytest.raises(ValueError):
        ExecutionMetadata("req", -1.0)


# ---------------------------------------------------------------------------
# ExecutionOutcome
# ---------------------------------------------------------------------------


def test_outcome_value_is_fresh_and_json_safe():
    outcome = ExecutionOutcome(
        value={"sources": [{"url": "https://example.com", "provider": "tavily"}]},
        attempts=(success_attempt("web_search", "tavily", elapsed_ms=1.0, result_count=1),),
        provider="tavily",
    )
    first = outcome.value
    assert isinstance(first, dict)
    assert isinstance(first["sources"], list)
    first["sources"][0]["url"] = "mutated"
    assert outcome.value["sources"][0]["url"] == "https://example.com"
    assert isinstance(outcome.attempts, tuple)
    assert outcome.provider == "tavily"


def test_outcome_rejects_non_json_and_non_string_keys():
    with pytest.raises(ValueError):
        ExecutionOutcome(value={"ok": object()})
    with pytest.raises(ValueError):
        ExecutionOutcome(value={1: "x"})
    with pytest.raises(ValueError):
        ExecutionOutcome(value={"nan": float("nan")})
    with pytest.raises(ValueError):
        ExecutionOutcome(value={"ok": True}, provider=123)


def test_outcome_accepts_list_of_attempts_and_coerces():
    outcome = ExecutionOutcome(
        value=[],
        attempts=[success_attempt("web_search", "tavily", elapsed_ms=1.0, result_count=0)],
    )
    assert isinstance(outcome.attempts, tuple)
    assert len(outcome.attempts) == 1


def test_outcome_rejects_non_execution_attempt_values():
    with pytest.raises(ValueError):
        ExecutionOutcome(value=[], attempts=[{"capability": "web_search"}])  # type: ignore[list-item]
    with pytest.raises(ValueError):
        ExecutionOutcome(value=[], attempts=("not-an-attempt",))  # type: ignore[arg-type]


def test_outcome_is_truly_immutable():
    attempt = success_attempt("web_search", "tavily", elapsed_ms=1.0, result_count=1)
    outcome = ExecutionOutcome(value=[], attempts=(attempt,), provider="tavily")
    assert outcome.provider == "tavily"
    with pytest.raises(AttributeError):
        outcome.provider = "other"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        outcome.attempts = ()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        outcome.value = []  # type: ignore[misc]
    assert outcome.provider == "tavily"
    assert outcome.attempts[0] is attempt


def test_outcome_attempts_immutable_and_independent():
    attempt = success_attempt("web_search", "tavily", elapsed_ms=1.0, result_count=1)
    outcome = ExecutionOutcome(value=[], attempts=(attempt,))
    assert outcome.attempts[0] is attempt
    assert isinstance(outcome.attempts, tuple)


# ---------------------------------------------------------------------------
# Forbidden import AST gate
# ---------------------------------------------------------------------------


def test_primitive_module_forbidden_imports():
    """The shared primitive module must import only stdlib plus security."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.append(node.module)
    forbidden_prefixes = (
        "smart_search.cli",
        "smart_search.cli_contract",
        "smart_search.cli_dispatch",
        "smart_search.cli_render",
        "smart_search.cli_parser",
        "smart_search.v2_contract",
        "smart_search.control_plane_contract",
        "smart_search.config",
        "smart_search.capability_service",
        "smart_search.capability_executor",
        "smart_search.runtime_cache",
        "smart_search.operation_runtime",
        "smart_search.search_service",
        "smart_search.research_service",
        "smart_search.service_support",
        "smart_search.service",
        "smart_search.providers",
    )
    for module in imported:
        assert not any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden_prefixes), (
            f"forbidden import: {module}"
        )
    allowed = {"smart_search.security"}
    others = [m for m in imported if m == "smart_search.security" or "." not in m]
    assert others  # stdlib + security present


RESEARCH_SERVICE_PATH = Path("src/smart_search/research_service.py")


def _is_execute_capability_call(node: ast.AST) -> bool:
    """True when ``node`` is an (optionally awaited) execute_capability(...) call."""
    if isinstance(node, ast.Await):
        node = node.value
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "execute_capability"
    )


def _executor_var_names(subtree: ast.AST) -> set[str]:
    """Names bound to an execute_capability(...) call within a subtree."""
    names: set[str] = set()
    for node in ast.walk(subtree):
        if isinstance(node, ast.Assign):
            value = node.value
            if _is_execute_capability_call(value):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if _is_execute_capability_call(node.value) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def _attribute_uses(subtree: ast.AST, var: str, attr: str) -> list[ast.Attribute]:
    """All ``var.attr`` attribute accesses within a subtree."""
    uses: list[ast.Attribute] = []
    for node in ast.walk(subtree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == attr
            and isinstance(node.value, ast.Name)
            and node.value.id == var
        ):
            uses.append(node)
    return uses


def _target_consumer_functions(tree: ast.AST) -> list[ast.FunctionDef]:
    """Return the generic typed-Evidence docs discovery consumer definition."""
    names = {"_run_research_docs_discovery"}
    return [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]


def _is_inside_project_attempts_call(node: ast.AST) -> bool:
    """True when ``node`` is an argument to a project_attempts_dict(...) call."""
    parent = getattr(node, "_parent", None)
    return (
        isinstance(parent, ast.Call)
        and isinstance(parent.func, ast.Name)
        and parent.func.id == "project_attempts_dict"
    )


def _link_parents(tree: ast.AST) -> None:
    for child in ast.iter_child_nodes(tree):
        child._parent = tree  # type: ignore[attr-defined]
        _link_parents(child)




def test_research_docs_discovery_projects_typed_attempts_through_legacy_boundary():
    """The live research docs stage must compose the typed Evidence owner and
    project typed attempts to dicts via the single project_attempts_dict
    boundary; raw typed tuples must never leak into legacy collections, and
    the removed Context7/Exa/AnySearch provider-specific research callbacks
    must not exist.
    """
    source = RESEARCH_SERVICE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    _link_parents(tree)

    # The legacy projection helper and the typed Evidence owner must be used.
    assert "project_attempts_dict" in source, "research_service must import project_attempts_dict"
    assert "docs_discovery" in source, "research_service must compose the typed docs_discovery owner"

    consumers = _target_consumer_functions(tree)
    assert len(consumers) == 1, (
        f"expected exactly one typed docs_discovery consumer, got {len(consumers)}"
    )

    for fn in consumers:
        # The typed owner outcome is bound and its attempts projected.
        assert "project_attempts_dict" in ast.unparse(fn), (
            f"{fn.name} must project typed attempts via project_attempts_dict"
        )
        outcome_uses = _attribute_uses(fn, "outcome", "attempts")
        assert outcome_uses, f"no .attempts access on the typed outcome in {fn.name}"
        for use in outcome_uses:
            assert _is_inside_project_attempts_call(use), (
                f"outcome.attempts must be projected via project_attempts_dict(...) "
                f"before entering a legacy collection in {fn.name} (line {use.lineno})"
            )
        assert not _executor_var_names(fn), (
            f"{fn.name} must not bind a direct execute_capability(...) executor result"
        )

    # The removed provider-specific research callbacks are gone entirely.
    for fn in (
        "_run_research_context7_docs",
        "_run_research_exa_docs",
        "_run_research_vertical_search",
    ):
        assert fn not in source, f"removed research callback {fn!r} must not remain"
