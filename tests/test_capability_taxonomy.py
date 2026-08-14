"""Deterministic offline contracts for v2 capability taxonomy and qualification.

Phase 1: additive metadata only. These tests must not depend on credentials or
network, and must prove v1 registry/profile/CLI surfaces stay unchanged.
"""

from __future__ import annotations

import copy

import pytest

from smart_search import capability_service, capability_taxonomy, service_support
from smart_search.capability_taxonomy import (
    FORBIDDEN_V2_CAPABILITY_IDS,
    PROVIDER_QUALIFICATIONS,
    V1_TO_V2_CAPABILITY_MAP,
    V2_CAPABILITY_DESCRIPTORS,
    V2_CAPABILITY_IDS,
    V2_CONTRACT_VERSION,
    classify_content_fetch_outcome,
    cross_capability_fallback_allowed,
    get_provider_qualification,
    get_v2_descriptor,
    is_answer_synthesis_input,
    is_content_fetch_success,
    is_docs_discovery_candidate,
    is_provider_qualified,
    is_structured_discovery_candidate,
    iter_v2_descriptors,
    list_provider_qualifications,
    list_v2_capabilities,
    map_v1_to_v2_capability,
    natural_language_answer_qualifies_for_source_discovery,
    same_capability_fallback_allowed,
    v1_to_v2_mapping,
    v2_availability_by_tier,
    v2_core_availability,
    validate_taxonomy_invariants,
)
from smart_search.cli_parser import build_parser


# ---------------------------------------------------------------------------
# Descriptor / mapping invariants
# ---------------------------------------------------------------------------


def test_v2_capability_ids_are_exactly_the_five_stable_names():
    assert list_v2_capabilities() == [
        "source_discovery",
        "docs_discovery",
        "content_fetch",
        "site_discovery",
        "answer_synthesis",
    ]
    assert tuple(list_v2_capabilities()) == V2_CAPABILITY_IDS
    assert len(set(V2_CAPABILITY_IDS)) == 5


def test_v2_descriptors_are_complete_and_enumerable():
    descriptors = iter_v2_descriptors()
    assert [item["id"] for item in descriptors] == list(V2_CAPABILITY_IDS)

    for descriptor in descriptors:
        assert set(capability_taxonomy.REQUIRED_DESCRIPTOR_FIELDS).issubset(descriptor)
        assert descriptor["contract_version"] == V2_CONTRACT_VERSION
        assert descriptor["tier"] in capability_taxonomy.TIERS
        assert descriptor["stability"] in capability_taxonomy.STABILITIES
        assert descriptor["fallback_scope"] == descriptor["id"]
        assert isinstance(descriptor["input_shape"], dict)
        assert isinstance(descriptor["normalized_output"], dict)
        assert descriptor["success_condition"]
        assert descriptor["empty_result_semantics"]
        assert descriptor["attempts_fallback_semantics"]
        assert descriptor["evidence_requirement"]
        # Copies must not alias the module constant.
        descriptor["id"] = "mutated"
        descriptor["input_shape"]["required_fields"].append("leak")
        assert get_v2_descriptor(descriptor["id"]) is None
        original = get_v2_descriptor("source_discovery")
        assert original is not None
        assert "leak" not in original["input_shape"]["required_fields"]


def test_v2_taxonomy_forbids_legacy_mixed_names_and_provider_ids():
    assert not (set(V2_CAPABILITY_IDS) & FORBIDDEN_V2_CAPABILITY_IDS)
    for capability_id in V2_CAPABILITY_IDS:
        assert capability_id not in {
            "main_search",
            "web_search",
            "web_fetch",
            "docs_search",
            "site_map",
            "tavily",
            "jina",
            "context7",
            "exa",
            "openai-compatible",
            "xai-responses",
        }
    # Provider ids appear only in qualification metadata, never as capability ids.
    provider_ids = {key[0] for key in PROVIDER_QUALIFICATIONS}
    assert provider_ids.isdisjoint(set(V2_CAPABILITY_IDS))


def test_site_discovery_and_answer_synthesis_tiers():
    site = get_v2_descriptor("site_discovery")
    synthesis = get_v2_descriptor("answer_synthesis")
    assert site is not None and synthesis is not None
    assert site["tier"] == "advanced"
    assert site["stability"] == "stable"
    assert synthesis["stability"] == "optional_extension"
    assert synthesis["tier"] != "core" or synthesis["stability"] != "stable"
    core_ids = {
        item["id"]
        for item in iter_v2_descriptors()
        if item["tier"] == "core" and item["stability"] == "stable"
    }
    assert "answer_synthesis" not in core_ids
    assert "site_discovery" not in core_ids
    assert core_ids == {"source_discovery", "docs_discovery", "content_fetch"}


def test_explicit_v1_to_v2_mapping_is_complete_and_not_inferred():
    expected = {
        "main_search": "answer_synthesis",
        "web_search": "source_discovery",
        "docs_search": "docs_discovery",
        "web_fetch": "content_fetch",
        "site_map": "site_discovery",
        "synthesis": "answer_synthesis",
    }
    assert v1_to_v2_mapping() == expected
    assert V1_TO_V2_CAPABILITY_MAP == expected
    for v1_id, v2_id in expected.items():
        assert map_v1_to_v2_capability(v1_id) == v2_id
        assert v2_id in V2_CAPABILITY_IDS
    # main_search never maps to source_discovery by default.
    assert map_v1_to_v2_capability("main_search") == "answer_synthesis"
    assert map_v1_to_v2_capability("unknown_capability") is None
    # Mapping copy is detached.
    mapping = v1_to_v2_mapping()
    mapping["main_search"] = "source_discovery"
    assert map_v1_to_v2_capability("main_search") == "answer_synthesis"


def test_taxonomy_invariants_pass():
    assert validate_taxonomy_invariants() == []


# ---------------------------------------------------------------------------
# Independent provider qualification
# ---------------------------------------------------------------------------


def test_qualification_is_independent_per_provider_capability_pair():
    tavily_source = get_provider_qualification("tavily", "source_discovery")
    tavily_fetch = get_provider_qualification("tavily", "content_fetch")
    tavily_site = get_provider_qualification("tavily", "site_discovery")
    tavily_synth = get_provider_qualification("tavily", "answer_synthesis")

    assert tavily_source["qualified"] is True
    assert tavily_fetch["qualified"] is True
    assert tavily_site["qualified"] is True
    # No automatic promotion to unrelated capabilities.
    assert tavily_synth["qualified"] is False
    assert tavily_synth["reason"] == "no_independent_qualification_record"

    openai_synth = get_provider_qualification("openai-compatible", "answer_synthesis")
    openai_source = get_provider_qualification("openai-compatible", "source_discovery")
    assert openai_synth["qualified"] is True
    assert openai_source["qualified"] is False
    assert "natural-language" in openai_source["reason"]


def test_provider_phase1_tiering_assertions():
    # First-class contracts
    for provider, capability in (
        ("tavily", "source_discovery"),
        ("tavily", "content_fetch"),
        ("tavily", "site_discovery"),
        ("context7", "docs_discovery"),
        ("jina", "content_fetch"),
    ):
        record = get_provider_qualification(provider, capability)
        assert record["qualified"] is True
        assert record["first_class"] is True
        assert record["contract_version"] == V2_CONTRACT_VERSION

    # Exa promoted after contract coverage
    exa = get_provider_qualification("exa", "docs_discovery")
    assert exa["qualified"] is True
    assert "contract tests" in exa["reason"]

    # OpenAI-compatible / xAI are Optional answer_synthesis only
    for provider in ("openai-compatible", "xai-responses"):
        synth = get_provider_qualification(provider, "answer_synthesis")
        source = get_provider_qualification(provider, "source_discovery")
        docs = get_provider_qualification(provider, "docs_discovery")
        fetch = get_provider_qualification(provider, "content_fetch")
        assert synth["qualified"] is True
        assert synth["stability"] == "optional_extension"
        assert source["qualified"] is False
        assert docs["qualified"] is False
        assert fetch["qualified"] is False

    # Experimental remain outside Core qualification
    anysearch = get_provider_qualification("anysearch", "source_discovery")
    zread = get_provider_qualification("zhipu-mcp-zread", "docs_discovery")
    assert anysearch["experimental"] is True
    assert anysearch["qualified"] is False
    assert zread["experimental"] is True
    assert zread["qualified"] is False


def test_core_availability_excludes_optional_and_experimental():
    core = v2_core_availability()
    assert set(core) == {"source_discovery", "docs_discovery", "content_fetch"}
    assert "answer_synthesis" not in core
    assert "site_discovery" not in core

    flat_providers = {provider for providers in core.values() for provider in providers}
    assert "anysearch" not in flat_providers
    assert "zhipu-mcp-zread" not in flat_providers
    assert "openai-compatible" not in flat_providers
    assert "xai-responses" not in flat_providers
    assert "tavily" in core["source_discovery"]
    assert "tavily" in core["content_fetch"]
    assert "context7" in core["docs_discovery"]
    assert "jina" in core["content_fetch"]
    assert "exa" in core["docs_discovery"]

    by_tier = v2_availability_by_tier()
    assert "tavily" in by_tier["advanced"].get("site_discovery", [])
    assert "openai-compatible" in by_tier["optional_extension"].get("answer_synthesis", [])
    assert "xai-responses" in by_tier["optional_extension"].get("answer_synthesis", [])
    assert "main-search" not in by_tier["optional_extension"].get("answer_synthesis", [])
    experimental_providers = {
        provider for providers in by_tier["experimental"].values() for provider in providers
    }
    assert "anysearch" in experimental_providers
    assert "zhipu-mcp-zread" in experimental_providers


def test_unqualified_provider_is_rejected_for_capability():
    assert is_provider_qualified("openai-compatible", "source_discovery") is False
    assert is_provider_qualified("unknown-provider", "content_fetch") is False
    assert is_provider_qualified("context7", "content_fetch") is False
    assert is_provider_qualified("jina", "source_discovery") is False


def test_same_capability_fallback_boundary_and_no_cross_capability():
    assert same_capability_fallback_allowed("content_fetch", "tavily", "jina") is True
    assert same_capability_fallback_allowed("docs_discovery", "context7", "exa") is True
    # Unqualified peer cannot participate.
    assert same_capability_fallback_allowed("source_discovery", "tavily", "openai-compatible") is False
    # Cross-capability substitution is never authorized by taxonomy metadata.
    assert (
        cross_capability_fallback_allowed(
            "docs_discovery",
            "source_discovery",
            provider="context7",
        )
        is False
    )
    assert cross_capability_fallback_allowed("content_fetch", "source_discovery") is False
    assert cross_capability_fallback_allowed("source_discovery", "source_discovery") is False


def test_list_provider_qualifications_filters_without_sharing_state():
    all_records = list_provider_qualifications()
    tavily_only = list_provider_qualifications(provider="tavily")
    qualified = list_provider_qualifications(qualified_only=True)
    assert all_records
    assert all(item["provider"] == "tavily" for item in tavily_only)
    assert all(item["qualified"] for item in qualified)
    # Mutating returned records does not alter module state.
    tavily_only[0]["qualified"] = False
    assert is_provider_qualified("tavily", tavily_only[0]["capability"]) is True


# ---------------------------------------------------------------------------
# Candidate / evidence / empty / failure predicates
# ---------------------------------------------------------------------------


def test_source_discovery_requires_structured_candidate_not_nl_answer_with_url():
    good = {
        "identity": "cand-1",
        "url": "https://example.com/a",
        "title": "Example A",
        "provider": "tavily",
        "description": "snippet",
    }
    assert is_structured_discovery_candidate(good) is True

    nl_answer = {
        "content": "Here is what I found about the topic with sources.",
        "url": "https://example.com/mentioned",
        "provider": "openai-compatible",
        "result_kind": "natural_language_answer",
    }
    assert is_structured_discovery_candidate(nl_answer) is False
    assert natural_language_answer_qualifies_for_source_discovery(nl_answer) is False

    answer_only = {
        "content": "A long free-form answer that mentions https://example.com",
        "url": "https://example.com",
        "provider": "xai-responses",
        "answer_only": True,
    }
    assert is_structured_discovery_candidate(answer_only) is False
    assert natural_language_answer_qualifies_for_source_discovery(answer_only) is False

    missing_title = {
        "identity": "cand-2",
        "url": "https://example.com/b",
        "provider": "tavily",
    }
    assert is_structured_discovery_candidate(missing_title) is False


def test_docs_discovery_keeps_docs_boundary():
    docs = {
        "identity": "ctx-react",
        "url": "context7:react",
        "title": "React",
        "provider": "context7",
        "source_type": "docs",
    }
    news = {
        "identity": "news-1",
        "url": "https://news.example/today",
        "title": "Markets today",
        "provider": "context7",
        "source_type": "news",
    }
    generic_fallback = {
        "identity": "web-1",
        "url": "https://example.com",
        "title": "Generic",
        "provider": "context7",
        "generic_web_fallback": True,
    }
    generic = {
        "identity": "generic-1",
        "url": "https://example.com/generic",
        "title": "Generic result",
        "provider": "other-provider",
    }
    context7_resource = {
        "id": "/facebook/react",
        "title": "React",
        "provider": "context7",
    }
    assert is_docs_discovery_candidate(docs) is True
    assert is_docs_discovery_candidate(news) is False
    assert is_docs_discovery_candidate(generic_fallback) is False
    assert is_docs_discovery_candidate(generic) is False
    assert is_docs_discovery_candidate(context7_resource) is True


@pytest.mark.parametrize(
    ("item", "success", "outcome"),
    [
        (
            {
                "identity": "page-1",
                "url": "https://example.com/page",
                "provider": "jina",
                "content": "full body",
            },
            True,
            "success",
        ),
        (
            {
                "identity": "page-2",
                "url": "https://example.com/raw",
                "provider": "tavily",
                "raw_content": "raw body",
            },
            True,
            "success",
        ),
        (
            {
                "identity": "page-empty",
                "url": "https://example.com/empty",
                "provider": "jina",
                "content": "   ",
                "error_type": "empty",
            },
            False,
            "empty",
        ),
        (
            {
                "identity": "page-challenge",
                "url": "https://example.com/cf",
                "provider": "jina",
                "content": "Title: Just a moment...",
                "challenge_page": True,
            },
            False,
            "failure",
        ),
        (
            {
                "identity": "page-challenge-unflagged",
                "url": "https://example.com/cf-unflagged",
                "provider": "jina",
                "content": "Title: Just a moment... Checking if the site connection is secure.",
            },
            False,
            "failure",
        ),
        (
            {
                "identity": "page-timeout",
                "url": "https://example.com/slow",
                "provider": "tavily",
                "ok": False,
                "error_type": "timeout",
            },
            False,
            "failure",
        ),
        (
            {
                "url": "https://example.com/no-provider",
                "content": "body",
            },
            False,
            "failure",
        ),
        (None, False, "invalid"),
    ],
)
def test_content_fetch_success_empty_and_failure_boundaries(item, success, outcome):
    assert is_content_fetch_success(item) is success
    assert classify_content_fetch_outcome(item) == outcome


def test_discovery_candidates_cannot_satisfy_evidence_contract():
    candidate = {
        "identity": "cand-1",
        "url": "https://example.com/a",
        "title": "Example A",
        "provider": "tavily",
        "description": "snippet only",
    }
    assert is_structured_discovery_candidate(candidate) is True
    # Snippet-only discovery is not content_fetch success / evidence.
    assert is_content_fetch_success(candidate) is False
    assert classify_content_fetch_outcome(candidate) in {"empty", "failure"}


def test_answer_synthesis_requires_explicit_verified_fetched_evidence():
    evidence = {
        "url": "https://x",
        "content": "y",
        "provider": "jina",
        "verified": True,
        "evidence_status": "fetched",
    }
    assert is_answer_synthesis_input({"evidence_items": [evidence]}) is True
    assert is_answer_synthesis_input({"fetched_evidence": [evidence]}) is True
    assert is_answer_synthesis_input(
        {"evidence_items": [{"url": "https://x", "content": "y", "provider": "jina"}]}
    ) is False
    assert is_answer_synthesis_input(
        {
            "evidence_items": [
                {
                    "identity": "candidate",
                    "url": "https://x",
                    "title": "Candidate",
                    "provider": "tavily",
                }
            ]
        }
    ) is False
    assert is_answer_synthesis_input({"query": "what happened?"}) is False
    assert is_answer_synthesis_input({"evidence_items": []}) is False
    assert is_answer_synthesis_input(None) is False


# ---------------------------------------------------------------------------
# v1 non-regression: registry, profile, fallback, CLI, public facade
# ---------------------------------------------------------------------------


def test_v1_registry_profile_and_fallback_unchanged_by_taxonomy_import():
    # Snapshot v1 provider profile surface after taxonomy import.
    profiles = capability_service.provider_profiles()
    assert "tavily" in profiles
    assert profiles["tavily"]["fallback_order"]["web_search"] == 2
    assert profiles["tavily"]["fallback_order"]["web_fetch"] == 0
    assert profiles["tavily"]["fallback_order"]["site_map"] == 0
    assert service_support.PROVIDER_PROFILES["jina"]["capability"] == "web_fetch"
    assert service_support.PROVIDER_PROFILES["context7"]["capability"] == "docs_search"
    assert service_support.PROVIDER_PROFILES["xai-responses"]["capability"] == "main_search"

    assert capability_service.MAIN_SEARCH_FALLBACK_CHAIN == ["xai-responses", "openai-compatible"]
    assert capability_service._provider_chain("web_search") == [
        "zhipu",
        "zhipu-mcp",
        "tavily",
        "firecrawl",
    ]
    assert capability_service._provider_chain("web_fetch") == [
        "tavily",
        "jina",
        "zhipu-mcp-reader",
        "firecrawl",
    ]
    assert capability_service._provider_chain("docs_search") == ["context7", "exa"]
    assert capability_service._provider_chain("site_map") == ["tavily"]

    # Taxonomy must not rewrite v1 profiles in place.
    before = copy.deepcopy(service_support.PROVIDER_PROFILES)
    _ = list_v2_capabilities()
    _ = v2_core_availability()
    assert service_support.PROVIDER_PROFILES == before


def test_v1_capability_status_keys_remain_legacy_names():
    status = capability_service.get_capability_status()
    for key in (
        "main_search",
        "web_search",
        "docs_search",
        "web_fetch",
        "site_map",
        "vertical_search",
        "zread",
        "deep_research",
    ):
        assert key in status
    for v2_id in V2_CAPABILITY_IDS:
        assert v2_id not in status


def test_taxonomy_not_exported_from_service_facade():
    # The broad v1 service facade is removed; taxonomy and v2 APIs are never
    # re-exported from a legacy facade module.
    import pytest as _pytest

    with _pytest.raises(ImportError):
        import smart_search.service  # noqa: F401
    with _pytest.raises(ImportError):
        import smart_search.cli_contract  # noqa: F401


def test_qualification_does_not_participate_in_v1_eligibility():
    # Even when a provider is v2-qualified for source_discovery, v1 eligibility
    # still comes only from the v1 registry + configuration gates.
    tavily_v1 = capability_service._provider_availability("tavily", "web_search")
    assert tavily_v1["eligible"] is False  # no key in isolated test config
    assert is_provider_qualified("tavily", "source_discovery") is True

    openai_v1 = capability_service._provider_availability("openai-compatible", "main_search")
    assert openai_v1["eligible"] is False
    assert is_provider_qualified("openai-compatible", "answer_synthesis") is True
    assert is_provider_qualified("openai-compatible", "source_discovery") is False


def test_anonymous_jina_is_eligible_without_credential(monkeypatch):
    """Anonymous Jina with the default Reader endpoint is normal eligible
    ``web_fetch``; with a key it reports ``ready``; ``JINA_RESPOND_WITH``
    without a key stays a non-eligible ``config_error`` that never leaks the
    configured value."""
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    monkeypatch.delenv("JINA_RESPOND_WITH", raising=False)
    monkeypatch.delenv("JINA_READER_API_URL", raising=False)
    anonymous = capability_service._provider_availability("jina", "web_fetch")
    assert anonymous["configured"] is True
    assert anonymous["eligible"] is True
    assert anonymous["reason"] == "anonymous_ready"
    assert anonymous.get("anonymous") is True
    assert "JINA_API_KEY" not in anonymous

    monkeypatch.setenv("JINA_API_KEY", "key-secret")
    keyed = capability_service._provider_availability("jina", "web_fetch")
    assert keyed["configured"] is True
    assert keyed["eligible"] is True
    assert keyed["reason"] == "ready"
    assert "key-secret" not in str(keyed)

    monkeypatch.setenv("JINA_RESPOND_WITH", "readerlm-v2")
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    blocked = capability_service._provider_availability("jina", "web_fetch")
    assert blocked["configured"] is False
    assert blocked["eligible"] is False
    assert blocked["reason"] == "config_error"
    assert "readerlm-v2" not in str(blocked)


def test_anonymous_jina_enters_fetch_only_never_discovery(monkeypatch):
    """Anonymous eligibility applies only to the declared capability:
    ``web_fetch``. Jina never becomes a discovery provider."""
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    fetch_status = capability_service._provider_availability("jina", "web_fetch")
    assert fetch_status["eligible"] is True
    assert fetch_status["reason"] == "anonymous_ready"
    for capability in ("web_search", "docs_search", "site_map"):
        status = capability_service._provider_availability("jina", capability)
        assert status["eligible"] is False
        assert status["reason"].startswith("unsupported_capability")


def test_no_model_core_is_ready_and_optional_llm_state_is_visible(monkeypatch):
    """With discovery plus fetch configured and no model routes, the Core
    minimum profile is ready while ``llm_synthesis``/``llm_plan`` stay
    explicit empty optional state. ``main_search`` remains the compatibility
    alias for the optional model routes."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("EXA_API_KEY", "exa-test")
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    monkeypatch.delenv("SMART_SEARCH_MODEL_ROUTES", raising=False)

    status = capability_service.get_capability_status()
    # Core group: source discovery (web_search OR docs_search) + web_fetch.
    assert status["web_search"]["ok"] is True  # Tavily
    assert status["docs_search"]["ok"] is True  # Exa
    assert status["web_fetch"]["ok"] is True  # Tavily and anonymous Jina
    assert status["main_search"]["ok"] is False  # no model route
    assert status["llm_synthesis"]["ok"] is False
    assert status["llm_synthesis"]["configured"] == []
    assert status["llm_synthesis"]["legacy_alias_of"] == "main_search"
    assert status["llm_synthesis"]["optional"] is True
    assert status["llm_plan"]["ok"] is False
    assert status["llm_plan"]["optional"] is True
    assert "no configured llm_plan capability" in str(status["llm_plan"])
    assert status["deep_research"]["ok"] is True
    assert status["deep_research"]["configured"] == ["tavily", "exa", "jina"]

    standard = capability_service._minimum_profile_result("standard", status)
    assert standard["ok"] is True
    assert standard["missing_required"] == []
    assert "main_search" not in standard["required"]

    full = capability_service._minimum_profile_result("full", status)
    # full additionally requires site mapping (Tavily)
    assert full["ok"] is True

    lite = capability_service._minimum_profile_result("lite", status)
    assert lite["ok"] is True
