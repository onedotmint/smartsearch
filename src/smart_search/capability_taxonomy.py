"""Additive v2 capability taxonomy and Provider qualification metadata.

Phase 1 only: pure descriptive contracts and offline predicates. This module
must not alter v1 registry, profile, provider order, fallback, CLI, or JSON.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from .logger import logger

# ---------------------------------------------------------------------------
# Stable identifiers and contract version
# ---------------------------------------------------------------------------

V2_CONTRACT_VERSION = "v2-capability-taxonomy-1"

V2_CAPABILITY_IDS: tuple[str, ...] = (
    "source_discovery",
    "docs_discovery",
    "content_fetch",
    "site_discovery",
    "answer_synthesis",
)

# Forbidden as v2 capability ids (legacy mixed names / brand surface).
FORBIDDEN_V2_CAPABILITY_IDS: frozenset[str] = frozenset(
    {
        "main_search",
        "web_search",
        "web_fetch",
        "docs_search",
        "site_map",
        "synthesis",
        "vertical_search",
        "zread",
    }
)

# ---------------------------------------------------------------------------
# Tiers and stability labels
# ---------------------------------------------------------------------------

TIER_CORE = "core"
TIER_ADVANCED = "advanced"
TIERS: frozenset[str] = frozenset({TIER_CORE, TIER_ADVANCED})

STABILITY_STABLE = "stable"
STABILITY_OPTIONAL_EXTENSION = "optional_extension"
STABILITY_EXPERIMENTAL = "experimental"
STABILITIES: frozenset[str] = frozenset(
    {
        STABILITY_STABLE,
        STABILITY_OPTIONAL_EXTENSION,
        STABILITY_EXPERIMENTAL,
    }
)

# ---------------------------------------------------------------------------
# Explicit v1 -> v2 mapping (not inferred by string similarity)
# ---------------------------------------------------------------------------

V1_TO_V2_CAPABILITY_MAP: dict[str, str] = {
    "main_search": "answer_synthesis",
    "web_search": "source_discovery",
    "docs_search": "docs_discovery",
    "web_fetch": "content_fetch",
    "site_map": "site_discovery",
    "synthesis": "answer_synthesis",
}

# Experimental extensions outside the stable taxonomy ids.
EXPERIMENTAL_V1_EXTENSIONS: dict[str, dict[str, Any]] = {
    "vertical_search": {
        "v2_extension": "source_discovery",
        "stability": STABILITY_EXPERIMENTAL,
        "note": "domain-constrained experimental extension of source_discovery",
    },
    "zread": {
        "v2_extension": None,
        "stability": STABILITY_EXPERIMENTAL,
        "note": "explicit repository/docs commands only; not a stable taxonomy capability",
    },
}


def _descriptor(
    *,
    capability_id: str,
    tier: str,
    stability: str,
    input_shape: dict[str, Any],
    normalized_output: dict[str, Any],
    success_condition: str,
    empty_result_semantics: str,
    attempts_fallback_semantics: str,
    evidence_requirement: str,
    fallback_scope: str,
) -> dict[str, Any]:
    return {
        "id": capability_id,
        "tier": tier,
        "stability": stability,
        "contract_version": V2_CONTRACT_VERSION,
        "input_shape": dict(input_shape),
        "normalized_output": dict(normalized_output),
        "success_condition": success_condition,
        "empty_result_semantics": empty_result_semantics,
        "attempts_fallback_semantics": attempts_fallback_semantics,
        "evidence_requirement": evidence_requirement,
        "fallback_scope": fallback_scope,
    }


V2_CAPABILITY_DESCRIPTORS: dict[str, dict[str, Any]] = {
    "source_discovery": _descriptor(
        capability_id="source_discovery",
        tier=TIER_CORE,
        stability=STABILITY_STABLE,
        input_shape={
            "type": "query",
            "required_fields": ["query"],
            "optional_fields": ["max_results", "filters"],
        },
        normalized_output={
            "type": "discovery_candidates",
            "item_fields": ["identity", "url", "title", "provider"],
            "optional_item_fields": ["description", "snippet", "published_date", "source_type"],
        },
        success_condition=(
            "Returns one or more structured candidates that each include stable identity, "
            "URL, display title, and Provider provenance."
        ),
        empty_result_semantics=(
            "Zero candidates with status empty is a non-success outcome that may trigger "
            "same-capability fallback; it is not a successful discovery result."
        ),
        attempts_fallback_semantics=(
            "Fallback only among providers qualified for source_discovery. "
            "Errors and empty results stay in this capability."
        ),
        evidence_requirement=(
            "Candidates are discovery only and must not generate citations. "
            "Fetched/read evidence with provenance is required before claim-level use."
        ),
        fallback_scope="source_discovery",
    ),
    "docs_discovery": _descriptor(
        capability_id="docs_discovery",
        tier=TIER_CORE,
        stability=STABILITY_STABLE,
        input_shape={
            "type": "docs_query",
            "required_fields": ["query"],
            "optional_fields": ["library_id", "domains", "max_results"],
        },
        normalized_output={
            "type": "docs_candidates",
            "item_fields": ["identity", "url", "title", "provider"],
            "optional_item_fields": ["description", "library_id", "version", "source_type"],
            "semantic_boundary": "docs/API/paper/trusted technical resource",
        },
        success_condition=(
            "Returns structured docs/API/paper or trusted technical resource candidates "
            "with identity, URL or resource id, display fields, and provenance."
        ),
        empty_result_semantics=(
            "Empty docs results remain docs_discovery empty outcomes and never justify "
            "generic web_search/source_discovery substitution."
        ),
        attempts_fallback_semantics=(
            "Fallback only among providers qualified for docs_discovery. "
            "Not a generic web discovery fallback chain."
        ),
        evidence_requirement=(
            "Docs candidates remain discovery until read/fetched content is admitted "
            "with provenance; snippets alone are not claim-level evidence."
        ),
        fallback_scope="docs_discovery",
    ),
    "content_fetch": _descriptor(
        capability_id="content_fetch",
        tier=TIER_CORE,
        stability=STABILITY_STABLE,
        input_shape={
            "type": "resource",
            "required_fields": ["url"],
            "optional_fields": ["resource_id", "accept"],
        },
        normalized_output={
            "type": "fetched_evidence",
            "item_fields": ["identity", "url", "content", "provider"],
            "optional_item_fields": ["title", "raw_content", "content_len", "source_type"],
        },
        success_condition=(
            "Returns non-empty body content for a stable resource identity with Provider "
            "provenance. Empty body, challenge pages, and transport failures are not success."
        ),
        empty_result_semantics=(
            "Empty body is classified as empty or quality failure and may fallback "
            "within content_fetch only; it must not be masked as successful evidence."
        ),
        attempts_fallback_semantics=(
            "Fallback only among providers qualified for content_fetch. "
            "Preserve error_type classification for challenge/auth/timeout/network."
        ),
        evidence_requirement=(
            "Only non-empty fetched/read content with resource identity and provenance "
            "satisfies the evidence contract and may produce citations."
        ),
        fallback_scope="content_fetch",
    ),
    "site_discovery": _descriptor(
        capability_id="site_discovery",
        tier=TIER_ADVANCED,
        stability=STABILITY_STABLE,
        input_shape={
            "type": "site_url",
            "required_fields": ["url"],
            "optional_fields": ["max_depth", "max_pages"],
        },
        normalized_output={
            "type": "site_candidates",
            "item_fields": ["identity", "url", "title", "provider"],
            "optional_item_fields": ["path", "description", "source_type"],
        },
        success_condition=(
            "Returns site structure or page candidates with identity, URL, display fields, "
            "and provenance. Advanced convenience, not Core availability."
        ),
        empty_result_semantics=(
            "Empty site maps are empty outcomes within site_discovery and do not "
            "promote into Core source_discovery."
        ),
        attempts_fallback_semantics=(
            "Fallback only among providers qualified for site_discovery."
        ),
        evidence_requirement=(
            "Site candidates are discovery only; page proof still requires content_fetch."
        ),
        fallback_scope="site_discovery",
    ),
    "answer_synthesis": _descriptor(
        capability_id="answer_synthesis",
        tier=TIER_ADVANCED,
        stability=STABILITY_OPTIONAL_EXTENSION,
        input_shape={
            "type": "evidence_input",
            "required_fields": ["evidence_items"],
            "optional_fields": ["query", "constraints"],
            "note": "Consumes explicit fetched/read evidence; not a Core discovery path.",
        },
        normalized_output={
            "type": "synthesized_answer",
            "item_fields": ["content", "provider"],
            "optional_item_fields": ["citations", "degraded", "gaps"],
        },
        success_condition=(
            "Produces natural-language output only from explicit evidence inputs. "
            "Optional Extension: not part of Core availability."
        ),
        empty_result_semantics=(
            "Missing or empty synthesis content is a synthesis failure/degradation and "
            "must preserve provided evidence rather than invent sources."
        ),
        attempts_fallback_semantics=(
            "Fallback only among providers qualified for answer_synthesis. "
            "Natural-language answers with URLs do not grant source_discovery."
        ),
        evidence_requirement=(
            "Synthesis consumes fetched/read evidence already admitted with provenance. "
            "It must not promote discovery candidates into citations."
        ),
        fallback_scope="answer_synthesis",
    ),
}


# ---------------------------------------------------------------------------
# Per-(provider, capability) qualification baseline for Phase 1
# ---------------------------------------------------------------------------

def _qualification(
    *,
    provider: str,
    capability: str,
    qualified: bool,
    tier: str,
    stability: str,
    reason: str,
    first_class: bool = False,
    experimental: bool = False,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "capability": capability,
        "qualified": bool(qualified),
        "contract_version": V2_CONTRACT_VERSION,
        "tier": tier,
        "stability": stability,
        "reason": reason,
        "first_class": bool(first_class),
        "experimental": bool(experimental),
        "eligibility_reason": reason,
    }


# Composite key helper
def qualification_key(provider: str, capability: str) -> tuple[str, str]:
    return (str(provider), str(capability))


PROVIDER_QUALIFICATIONS: dict[tuple[str, str], dict[str, Any]] = {
    # Tavily first-class multi-capability
    ("tavily", "source_discovery"): _qualification(
        provider="tavily",
        capability="source_discovery",
        qualified=True,
        tier=TIER_CORE,
        stability=STABILITY_STABLE,
        reason="first-class web discovery candidate contract",
        first_class=True,
    ),
    ("tavily", "content_fetch"): _qualification(
        provider="tavily",
        capability="content_fetch",
        qualified=True,
        tier=TIER_CORE,
        stability=STABILITY_STABLE,
        reason="first-class URL extract/fetch contract with non-empty body requirement",
        first_class=True,
    ),
    ("tavily", "site_discovery"): _qualification(
        provider="tavily",
        capability="site_discovery",
        qualified=True,
        tier=TIER_ADVANCED,
        stability=STABILITY_STABLE,
        reason="first-class site_map contract; Advanced capability only",
        first_class=True,
    ),
    # Context7 first-class docs
    ("context7", "docs_discovery"): _qualification(
        provider="context7",
        capability="docs_discovery",
        qualified=True,
        tier=TIER_CORE,
        stability=STABILITY_STABLE,
        reason="first-class library/API/framework docs discovery contract",
        first_class=True,
    ),
    # Jina first-class fetch-only
    ("jina", "content_fetch"): _qualification(
        provider="jina",
        capability="content_fetch",
        qualified=True,
        tier=TIER_CORE,
        stability=STABILITY_STABLE,
        reason="first-class keyed fetch-only contract; challenge/empty body fail closed",
        first_class=True,
    ),
    # Exa promoted after candidate/error/empty contract coverage exists
    ("exa", "docs_discovery"): _qualification(
        provider="exa",
        capability="docs_discovery",
        qualified=True,
        tier=TIER_CORE,
        stability=STABILITY_STABLE,
        reason="docs discovery promoted after candidate/error/empty contract tests",
        first_class=False,
    ),
    # Optional regional/general discovery and fetch (not experimental; not first-class)
    ("zhipu", "source_discovery"): _qualification(
        provider="zhipu",
        capability="source_discovery",
        qualified=True,
        tier=TIER_CORE,
        stability=STABILITY_STABLE,
        reason="optional Chinese/current web discovery; independent source_discovery only",
    ),
    ("zhipu-mcp", "source_discovery"): _qualification(
        provider="zhipu-mcp",
        capability="source_discovery",
        qualified=True,
        tier=TIER_CORE,
        stability=STABILITY_STABLE,
        reason="optional Coding Plan MCP web discovery; independent source_discovery only",
    ),
    ("zhipu-mcp-reader", "content_fetch"): _qualification(
        provider="zhipu-mcp-reader",
        capability="content_fetch",
        qualified=True,
        tier=TIER_CORE,
        stability=STABILITY_STABLE,
        reason="optional Coding Plan MCP reader fetch contract",
    ),
    ("firecrawl", "source_discovery"): _qualification(
        provider="firecrawl",
        capability="source_discovery",
        qualified=True,
        tier=TIER_CORE,
        stability=STABILITY_STABLE,
        reason="optional JS-heavy/dynamic discovery fallback within source_discovery",
    ),
    ("firecrawl", "content_fetch"): _qualification(
        provider="firecrawl",
        capability="content_fetch",
        qualified=True,
        tier=TIER_CORE,
        stability=STABILITY_STABLE,
        reason="optional JS-heavy/dynamic fetch fallback within content_fetch",
    ),
    # OpenAI-compatible / xAI: Optional answer_synthesis only
    ("xai-responses", "answer_synthesis"): _qualification(
        provider="xai-responses",
        capability="answer_synthesis",
        qualified=True,
        tier=TIER_ADVANCED,
        stability=STABILITY_OPTIONAL_EXTENSION,
        reason="optional extension: main_search natural-language answers map to answer_synthesis only",
    ),
    ("openai-compatible", "answer_synthesis"): _qualification(
        provider="openai-compatible",
        capability="answer_synthesis",
        qualified=True,
        tier=TIER_ADVANCED,
        stability=STABILITY_OPTIONAL_EXTENSION,
        reason="optional extension: main_search natural-language answers map to answer_synthesis only",
    ),
    # Explicit non-qualifications: NL answer providers do not auto-gain discovery
    ("xai-responses", "source_discovery"): _qualification(
        provider="xai-responses",
        capability="source_discovery",
        qualified=False,
        tier=TIER_CORE,
        stability=STABILITY_STABLE,
        reason="natural-language answers with URLs do not satisfy structured candidate contract",
    ),
    ("openai-compatible", "source_discovery"): _qualification(
        provider="openai-compatible",
        capability="source_discovery",
        qualified=False,
        tier=TIER_CORE,
        stability=STABILITY_STABLE,
        reason="natural-language answers with URLs do not satisfy structured candidate contract",
    ),
    # Experimental providers remain experimental and outside Core availability
    ("anysearch", "source_discovery"): _qualification(
        provider="anysearch",
        capability="source_discovery",
        qualified=False,
        tier=TIER_CORE,
        stability=STABILITY_EXPERIMENTAL,
        reason="experimental vertical discovery; not Phase 1 Core-qualified",
        experimental=True,
    ),
    ("zhipu-mcp-zread", "docs_discovery"): _qualification(
        provider="zhipu-mcp-zread",
        capability="docs_discovery",
        qualified=False,
        tier=TIER_CORE,
        stability=STABILITY_EXPERIMENTAL,
        reason="experimental zread remains explicit-only and never general docs fallback",
        experimental=True,
    ),
}


# ---------------------------------------------------------------------------
# Query APIs (internal / additive; not wired into v1 routes)
# ---------------------------------------------------------------------------

REQUIRED_DESCRIPTOR_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "tier",
        "stability",
        "contract_version",
        "input_shape",
        "normalized_output",
        "success_condition",
        "empty_result_semantics",
        "attempts_fallback_semantics",
        "evidence_requirement",
        "fallback_scope",
    }
)


def list_v2_capabilities() -> list[str]:
    """Return the stable ordered list of v2 capability ids."""
    return list(V2_CAPABILITY_IDS)


def get_v2_descriptor(capability: str) -> dict[str, Any] | None:
    """Return a copy of one v2 capability descriptor, or None."""
    descriptor = V2_CAPABILITY_DESCRIPTORS.get(capability)
    return copy.deepcopy(descriptor) if descriptor else None


def iter_v2_descriptors() -> list[dict[str, Any]]:
    """Enumerate all v2 descriptors as independent dict copies."""
    return [copy.deepcopy(V2_CAPABILITY_DESCRIPTORS[capability_id]) for capability_id in V2_CAPABILITY_IDS]


def map_v1_to_v2_capability(v1_capability: str) -> str | None:
    """Map a v1 capability id to its v2 counterpart when defined."""
    return V1_TO_V2_CAPABILITY_MAP.get(v1_capability)


def v1_to_v2_mapping() -> dict[str, str]:
    """Return a copy of the explicit v1-to-v2 mapping table."""
    return dict(V1_TO_V2_CAPABILITY_MAP)


def get_provider_qualification(provider: str, capability: str) -> dict[str, Any]:
    """
    Return the independent qualification record for (provider, capability).

    Missing pairs are not qualified; they never inherit another capability's result.
    """
    logger.info(
        "lookup provider qualification: provider=%s capability=%s",
        provider,
        capability,
    )
    record = PROVIDER_QUALIFICATIONS.get(qualification_key(provider, capability))
    if record is None:
        result = _qualification(
            provider=provider,
            capability=capability,
            qualified=False,
            tier=V2_CAPABILITY_DESCRIPTORS.get(capability, {}).get("tier", TIER_CORE),
            stability=V2_CAPABILITY_DESCRIPTORS.get(capability, {}).get(
                "stability", STABILITY_STABLE
            ),
            reason="no_independent_qualification_record",
        )
        logger.info(
            "provider qualification missing: provider=%s capability=%s",
            provider,
            capability,
        )
        return result
    return dict(record)


def is_provider_qualified(provider: str, capability: str) -> bool:
    """True only when the explicit (provider, capability) pair is qualified."""
    return bool(get_provider_qualification(provider, capability).get("qualified"))


def list_provider_qualifications(
    *,
    provider: str | None = None,
    capability: str | None = None,
    qualified_only: bool = False,
) -> list[dict[str, Any]]:
    """List qualification records, optionally filtered."""
    records: list[dict[str, Any]] = []
    for (record_provider, record_capability), record in PROVIDER_QUALIFICATIONS.items():
        if provider is not None and record_provider != provider:
            continue
        if capability is not None and record_capability != capability:
            continue
        if qualified_only and not record.get("qualified"):
            continue
        records.append(dict(record))
    records.sort(key=lambda item: (item["provider"], item["capability"]))
    return records


def v2_core_availability() -> dict[str, list[str]]:
    """
    Internal Core availability: qualified, non-experimental providers per Core capability.

    Optional Extension and Advanced capabilities are excluded from Core availability.
    Experimental providers never appear here even if they have a discovery-shaped result.
    """
    availability: dict[str, list[str]] = {}
    for capability_id in V2_CAPABILITY_IDS:
        descriptor = V2_CAPABILITY_DESCRIPTORS[capability_id]
        if descriptor["tier"] != TIER_CORE or descriptor["stability"] != STABILITY_STABLE:
            continue
        providers: list[str] = []
        for record in list_provider_qualifications(capability=capability_id, qualified_only=True):
            if record.get("experimental"):
                continue
            if record.get("stability") == STABILITY_EXPERIMENTAL:
                continue
            providers.append(record["provider"])
        availability[capability_id] = providers
    return availability


def v2_availability_by_tier() -> dict[str, dict[str, list[str]]]:
    """
    Internal availability split by Core / Advanced / Optional Extension / Experimental.

    Pure metadata; does not change v1 route selection.
    """
    buckets: dict[str, dict[str, list[str]]] = {
        "core": {},
        "advanced": {},
        "optional_extension": {},
        "experimental": {},
    }
    for capability_id, descriptor in V2_CAPABILITY_DESCRIPTORS.items():
        for record in list_provider_qualifications(capability=capability_id):
            provider = record["provider"]
            if record.get("experimental") or record.get("stability") == STABILITY_EXPERIMENTAL:
                bucket = "experimental"
            elif descriptor["stability"] == STABILITY_OPTIONAL_EXTENSION or record.get(
                "stability"
            ) == STABILITY_OPTIONAL_EXTENSION:
                bucket = "optional_extension"
            elif descriptor["tier"] == TIER_ADVANCED:
                bucket = "advanced"
            else:
                bucket = "core"
            if not record.get("qualified") and bucket != "experimental":
                # Keep explicit non-qualifications out of availability lists.
                continue
            buckets[bucket].setdefault(capability_id, [])
            if provider not in buckets[bucket][capability_id]:
                if record.get("qualified") or bucket == "experimental":
                    buckets[bucket][capability_id].append(provider)
    return buckets


def same_capability_fallback_allowed(capability: str, provider_a: str, provider_b: str) -> bool:
    """Fallback is allowed only when both providers are qualified for the same v2 capability."""
    if capability not in V2_CAPABILITY_DESCRIPTORS:
        return False
    return is_provider_qualified(provider_a, capability) and is_provider_qualified(
        provider_b, capability
    )


def cross_capability_fallback_allowed(
    from_capability: str,
    to_capability: str,
    *,
    provider: str = "",
) -> bool:
    """Always false: taxonomy metadata must never authorize cross-capability fallback."""
    del provider  # explicit key presence required by callers; value unused by design
    if from_capability == to_capability:
        return False
    return False


# ---------------------------------------------------------------------------
# Contract predicates for candidate / evidence / synthesis shapes
# ---------------------------------------------------------------------------

def _normalized(value: Any) -> str:
    return str(value or "").strip()


def _candidate_identity(item: Mapping[str, Any]) -> str:
    for key in ("identity", "id", "url"):
        value = _normalized(item.get(key))
        if value:
            return value
    return ""


def _is_structured_candidate(
    item: Mapping[str, Any] | None,
    *,
    require_url: bool = True,
) -> bool:
    if not isinstance(item, Mapping):
        return False
    # Reject answer-shaped payloads that only happen to include a URL field.
    if _normalized(item.get("content")) and not _normalized(item.get("title")) and not item.get(
        "results"
    ):
        if _normalized(item.get("url")) and not _normalized(item.get("identity")) and not _normalized(
            item.get("id")
        ):
            return False
    identity = _candidate_identity(item)
    url = _normalized(item.get("url"))
    title = _normalized(item.get("title"))
    provider = _normalized(item.get("provider"))
    if not identity or (require_url and not url) or not title or not provider:
        return False
    if item.get("answer_only") is True:
        return False
    if item.get("result_kind") == "natural_language_answer":
        return False
    return True


def is_structured_discovery_candidate(item: Mapping[str, Any] | None) -> bool:
    """
    source_discovery / site_discovery candidate contract.

    Requires stable identity, URL, display title, and Provider provenance.
    A natural-language answer that merely mentions URLs is not sufficient.
    """
    return _is_structured_candidate(item, require_url=True)


_DOCS_SOURCE_TYPES = frozenset({
    "api",
    "documentation",
    "docs",
    "library",
    "paper",
    "technical",
    "technical_resource",
})
_DOCS_NATIVE_PROVIDERS = frozenset({"context7", "exa"})


def is_docs_discovery_candidate(item: Mapping[str, Any] | None) -> bool:
    """Require explicit docs semantics or a provider-owned docs capability."""
    if not _is_structured_candidate(item, require_url=False):
        return False
    assert item is not None
    source_type = _normalized(item.get("source_type")).lower()
    if source_type in {"news", "generic_web", "social"}:
        return False
    if item.get("generic_web_fallback") is True:
        return False
    docs_marker = (
        source_type in _DOCS_SOURCE_TYPES
        or bool(_normalized(item.get("library_id")))
        or bool(_normalized(item.get("docs_url")))
        or _normalized(item.get("provider")).lower() in _DOCS_NATIVE_PROVIDERS
    )
    if not docs_marker:
        return False
    return bool(_normalized(item.get("url")) or _normalized(item.get("id")))


CHALLENGE_CONTENT_MARKERS = (
    "title: just a moment",
    "checking if the site connection is secure",
    "attention required! | cloudflare",
    "enable javascript and cookies to continue",
)


def _looks_like_challenge(body: str) -> bool:
    normalized = body.strip().lower()
    return bool(normalized) and any(marker in normalized for marker in CHALLENGE_CONTENT_MARKERS)


def is_content_fetch_success(item: Mapping[str, Any] | None) -> bool:
    """
    content_fetch success requires non-empty body, resource identity, and provenance.

    Empty body, challenge pages, and transport-classified failures are not success.
    """
    if not isinstance(item, Mapping):
        return False
    if item.get("ok") is False:
        return False
    error_type = _normalized(item.get("error_type")).lower()
    if error_type in {
        "empty",
        "quality_error",
        "auth_error",
        "timeout",
        "network_error",
        "protocol_error",
        "parse_error",
        "provider_error",
        "challenge",
    }:
        return False
    if item.get("challenge") is True or item.get("challenge_page") is True:
        return False
    identity = _candidate_identity(item)
    url = _normalized(item.get("url"))
    provider = _normalized(item.get("provider"))
    body = _normalized(item.get("content")) or _normalized(item.get("raw_content"))
    if _looks_like_challenge(body):
        return False
    if not identity or not url or not provider or not body:
        return False
    return True


def classify_content_fetch_outcome(item: Mapping[str, Any] | None) -> str:
    """
    Distinguish success / empty / failure for content_fetch outcomes.

    Returns one of: success, empty, failure, invalid.
    """
    if not isinstance(item, Mapping):
        return "invalid"
    if is_content_fetch_success(item):
        return "success"
    error_type = _normalized(item.get("error_type")).lower()
    body = _normalized(item.get("content")) or _normalized(item.get("raw_content"))
    if error_type == "empty" or (item.get("ok") is True and not body):
        return "empty"
    if item.get("challenge") is True or item.get("challenge_page") is True:
        return "failure"
    if error_type or item.get("ok") is False:
        return "failure"
    if not body:
        return "empty"
    return "failure"


def is_answer_synthesis_input(payload: Mapping[str, Any] | None) -> bool:
    """answer_synthesis requires explicit evidence inputs; bare query is insufficient."""
    if not isinstance(payload, Mapping):
        return False
    evidence = payload.get("evidence_items") or payload.get("fetched_evidence") or []
    if not isinstance(evidence, (list, tuple)) or not evidence:
        return False
    return all(
        is_content_fetch_success(item)
        and item.get("verified") is True
        and item.get("evidence_status") == "fetched"
        for item in evidence
        if isinstance(item, Mapping)
    ) and all(isinstance(item, Mapping) for item in evidence)


def natural_language_answer_qualifies_for_source_discovery(
    payload: Mapping[str, Any] | None,
) -> bool:
    """NL answers that merely include URLs never pass source_discovery qualification."""
    if not isinstance(payload, Mapping):
        return False
    if payload.get("result_kind") == "natural_language_answer":
        return False
    content = _normalized(payload.get("content"))
    has_url = bool(_normalized(payload.get("url"))) or bool(payload.get("urls"))
    if content and has_url and not is_structured_discovery_candidate(payload):
        return False
    return is_structured_discovery_candidate(payload)


def validate_taxonomy_invariants() -> list[str]:
    """Return a list of invariant violations (empty list means healthy)."""
    problems: list[str] = []
    if tuple(V2_CAPABILITY_DESCRIPTORS) != V2_CAPABILITY_IDS and set(
        V2_CAPABILITY_DESCRIPTORS
    ) != set(V2_CAPABILITY_IDS):
        problems.append("descriptor keys must match V2_CAPABILITY_IDS")
    for capability_id in V2_CAPABILITY_IDS:
        if capability_id in FORBIDDEN_V2_CAPABILITY_IDS:
            problems.append(f"forbidden capability id registered: {capability_id}")
        descriptor = V2_CAPABILITY_DESCRIPTORS.get(capability_id)
        if not descriptor:
            problems.append(f"missing descriptor: {capability_id}")
            continue
        missing = REQUIRED_DESCRIPTOR_FIELDS - set(descriptor)
        if missing:
            problems.append(f"{capability_id} missing fields: {sorted(missing)}")
        if descriptor.get("id") != capability_id:
            problems.append(f"{capability_id} id mismatch")
        if descriptor.get("tier") not in TIERS:
            problems.append(f"{capability_id} invalid tier")
        if descriptor.get("stability") not in STABILITIES:
            problems.append(f"{capability_id} invalid stability")
        if descriptor.get("fallback_scope") != capability_id:
            problems.append(f"{capability_id} fallback_scope must equal id")
        if capability_id == "site_discovery" and descriptor.get("tier") != TIER_ADVANCED:
            problems.append("site_discovery must be Advanced")
        if capability_id == "answer_synthesis":
            if descriptor.get("stability") != STABILITY_OPTIONAL_EXTENSION:
                problems.append("answer_synthesis must be optional_extension")
            if descriptor.get("tier") == TIER_CORE and descriptor.get("stability") == STABILITY_STABLE:
                problems.append("answer_synthesis must not be Core stable")
    for v1_id, v2_id in V1_TO_V2_CAPABILITY_MAP.items():
        if v2_id not in V2_CAPABILITY_DESCRIPTORS:
            problems.append(f"mapping {v1_id} -> unknown v2 id {v2_id}")
        if v2_id in FORBIDDEN_V2_CAPABILITY_IDS:
            problems.append(f"mapping target is forbidden id: {v2_id}")
    for (provider, capability), record in PROVIDER_QUALIFICATIONS.items():
        if capability not in V2_CAPABILITY_DESCRIPTORS:
            problems.append(f"qualification for unknown capability: {provider}/{capability}")
        if record.get("provider") != provider or record.get("capability") != capability:
            problems.append(f"qualification key mismatch: {provider}/{capability}")
        # Provider brand names must never be capability ids.
        if provider in V2_CAPABILITY_DESCRIPTORS:
            problems.append(f"provider id collides with capability id: {provider}")
    return problems
