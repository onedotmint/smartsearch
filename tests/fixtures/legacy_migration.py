"""Frozen persisted-data migration contract fixtures for the published 0.1.0 state.

Derived from the published ``v0.1.0`` tag config shape and the frozen legacy
surface inventory (the removal authority). This module is runtime-inert: it
freezes the approved five ``data-upgrade-only`` readers and the canonical
replacement strings for removed selectors and old Skill spellings. It must
never be imported by production code and never used as a dispatcher.

Rollback of any reader transform restores the preceding reader revision while
the source ``config.json`` bytes stay untouched; this fixture is deleted only
together with the inventory/scan artifacts after the migration window closes.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Published 0.1.0 persisted config shape
# ---------------------------------------------------------------------------
# The v0.1.0 tag accepted exactly these 69 keys (no SMART_SEARCH_MODEL_ROUTES).
# The order is the sorted key order extracted from the tag's config.py.
V010_CONFIG_KEYS: Final[tuple[str, ...]] = (
    "ANYSEARCH_API_KEY",
    "ANYSEARCH_API_URL",
    "ANYSEARCH_TIMEOUT_SECONDS",
    "CONTEXT7_API_KEY",
    "CONTEXT7_BASE_URL",
    "CONTEXT7_TIMEOUT_SECONDS",
    "EXA_API_KEY",
    "EXA_BASE_URL",
    "EXA_TIMEOUT_SECONDS",
    "FIRECRAWL_API_KEY",
    "FIRECRAWL_API_URL",
    "INTENT_CLASSIFIER_API_KEY",
    "INTENT_CLASSIFIER_API_URL",
    "INTENT_CLASSIFIER_MODEL",
    "INTENT_EMBEDDING_API_KEY",
    "INTENT_EMBEDDING_API_URL",
    "INTENT_EMBEDDING_MARGIN",
    "INTENT_EMBEDDING_MODEL",
    "INTENT_EMBEDDING_THRESHOLD",
    "INTENT_ROUTER_TIMEOUT_SECONDS",
    "JINA_API_KEY",
    "JINA_READER_API_URL",
    "JINA_RESPOND_WITH",
    "JINA_TIMEOUT_SECONDS",
    "OPENAI_COMPATIBLE_API_KEY",
    "OPENAI_COMPATIBLE_API_URL",
    "OPENAI_COMPATIBLE_FALLBACK_MODELS",
    "OPENAI_COMPATIBLE_MODEL",
    "OPENAI_COMPATIBLE_STREAM",
    "SMART_SEARCH_CACHE_ENABLED",
    "SMART_SEARCH_CACHE_MAX_SIZE",
    "SMART_SEARCH_DEBUG",
    "SMART_SEARCH_FALLBACK_MODE",
    "SMART_SEARCH_FETCH_CACHE_TTL_SECONDS",
    "SMART_SEARCH_FETCH_PROMPT_FILE",
    "SMART_SEARCH_INTENT_ROUTER",
    "SMART_SEARCH_LOG_DIR",
    "SMART_SEARCH_LOG_LEVEL",
    "SMART_SEARCH_LOG_TO_FILE",
    "SMART_SEARCH_MINIMUM_PROFILE",
    "SMART_SEARCH_OUTPUT_CLEANUP",
    "SMART_SEARCH_PROMPT_DIR",
    "SMART_SEARCH_RESEARCH_DISABLED_PROVIDERS",
    "SMART_SEARCH_RESEARCH_PREFERRED_PROVIDERS",
    "SMART_SEARCH_RESEARCH_PROMPT_FILE",
    "SMART_SEARCH_RETRY_MAX_ATTEMPTS",
    "SMART_SEARCH_RETRY_MAX_WAIT",
    "SMART_SEARCH_RETRY_MULTIPLIER",
    "SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS",
    "SMART_SEARCH_SEARCH_PROMPT_FILE",
    "SMART_SEARCH_VALIDATION_LEVEL",
    "SSL_VERIFY",
    "TAVILY_API_KEY",
    "TAVILY_API_URL",
    "TAVILY_ENABLED",
    "TAVILY_TIMEOUT_SECONDS",
    "XAI_API_KEY",
    "XAI_API_URL",
    "XAI_MODEL",
    "XAI_TOOLS",
    "ZHIPU_API_KEY",
    "ZHIPU_API_URL",
    "ZHIPU_MCP_API_KEY",
    "ZHIPU_MCP_READER_API_URL",
    "ZHIPU_MCP_SEARCH_API_URL",
    "ZHIPU_MCP_TIMEOUT_SECONDS",
    "ZHIPU_MCP_ZREAD_API_URL",
    "ZHIPU_SEARCH_ENGINE",
    "ZHIPU_TIMEOUT_SECONDS",
)

# Saved legacy main-search values as a published 0.1.0 file would contain them.
V010_SAVED_MAIN_SEARCH: Final[dict[str, str]] = {
    "XAI_API_URL": "https://api.x.ai/v1",
    "XAI_API_KEY": "xai-0-1-0-secret",
    "XAI_MODEL": "grok-4-fast",
    "XAI_TOOLS": "web_search,x_search",
    "OPENAI_COMPATIBLE_API_URL": "https://relay-a.example/v1",
    "OPENAI_COMPATIBLE_API_KEY": "openai-0-1-0-secret",
    "OPENAI_COMPATIBLE_MODEL": "qwen3-max",
    "OPENAI_COMPATIBLE_STREAM": "true",
    "OPENAI_COMPATIBLE_FALLBACK_MODELS": "qwen3-max-lite",
}

# Full published config: legacy main-search plus unrelated keys. Every value is
# the string form a 0.1.0 setup wrote. Use ``json.loads`` in tests; never import
# a runtime module to interpret it.
V010_CONFIG_JSON: Final[str] = (
    "{\n"
    '  "EXA_API_KEY": "exa-0-1-0-secret",\n'
    '  "OPENAI_COMPATIBLE_API_URL": "https://relay-a.example/v1",\n'
    '  "OPENAI_COMPATIBLE_API_KEY": "openai-0-1-0-secret",\n'
    '  "OPENAI_COMPATIBLE_MODEL": "qwen3-max",\n'
    '  "OPENAI_COMPATIBLE_STREAM": "true",\n'
    '  "OPENAI_COMPATIBLE_FALLBACK_MODELS": "qwen3-max-lite",\n'
    '  "SMART_SEARCH_CACHE_ENABLED": "false",\n'
    '  "SMART_SEARCH_FALLBACK_MODE": "auto",\n'
    '  "SMART_SEARCH_INTENT_ROUTER": "hybrid",\n'
    '  "SMART_SEARCH_MINIMUM_PROFILE": "standard",\n'
    '  "SMART_SEARCH_VALIDATION_LEVEL": "balanced",\n'
    '  "TAVILY_API_KEY": "tavily-0-1-0-secret",\n'
    '  "XAI_API_URL": "https://api.x.ai/v1",\n'
    '  "XAI_API_KEY": "xai-0-1-0-secret",\n'
    '  "XAI_MODEL": "grok-4-fast",\n'
    '  "XAI_TOOLS": "web_search,x_search",\n'
    '  "INTENT_EMBEDDING_THRESHOLD": "0.74",\n'
    '  "INTENT_EMBEDDING_MARGIN": "0.05",\n'
    '  "INTENT_ROUTER_TIMEOUT_SECONDS": "8",\n'
    '  "SSL_VERIFY": "true"\n'
    "}\n"
)

# A published config found in the simulated Windows legacy home location. Its
# distinct secrets prove the fallback reader selects this file.
V010_WINDOWS_LEGACY_HOME_CONFIG_JSON: Final[str] = (
    "{\n"
    '  "XAI_API_URL": "https://api.x.ai/v1",\n'
    '  "XAI_API_KEY": "xai-win-legacy-secret",\n'
    '  "XAI_MODEL": "grok-4-fast",\n'
    '  "XAI_TOOLS": "web_search",\n'
    '  "OPENAI_COMPATIBLE_API_URL": "https://relay-win.example/v1",\n'
    '  "OPENAI_COMPATIBLE_API_KEY": "openai-win-legacy-secret",\n'
    '  "OPENAI_COMPATIBLE_MODEL": "win-model"\n'
    "}\n"
)

# Current persisted route-list shape (the upgrade target and the retained
# V0.1.0+ reader input). Order is authoritative; ids are stable.
SAVED_MODEL_ROUTES: Final[dict[str, object]] = {
    "SMART_SEARCH_MODEL_ROUTES": [
        {
            "id": "primary",
            "provider": "openai-compatible",
            "api_url": "https://relay-a.example/v1",
            "api_key": "route-primary-secret",
            "model": "primary-model",
            "stream": False,
            "fallback_models": ["primary-model-lite"],
        },
        {
            "id": "backup",
            "provider": "xai-responses",
            "api_url": "https://api.x.ai/v1",
            "api_key": "route-backup-secret",
            "model": "grok-4-fast",
            "tools": ["web_search", "x_search"],
        },
    ]
}

# ---------------------------------------------------------------------------
# Removed selector spellings (kind=schema_selector, all disposition=remove)
# ---------------------------------------------------------------------------
# The replacement is frozen: omit the selector and let the canonical command
# domain decide the contract. The fixture never maps a spelling to a command.
SELECTOR_REPLACEMENT: Final[str] = "omit selector; route by canonical command domain"

REMOVED_SCHEMA_SELECTOR_SPELLINGS: Final[tuple[tuple[str, str], ...]] = (
    ("--schema-version", SELECTOR_REPLACEMENT),
    ("--schema-version 1", SELECTOR_REPLACEMENT),
    ("--schema-version 2", SELECTOR_REPLACEMENT),
    ("--schema-version 3", SELECTOR_REPLACEMENT),
    ("--schema-version=1", SELECTOR_REPLACEMENT),
    ("--schema-version=2", SELECTOR_REPLACEMENT),
    ("--schema-version=3", SELECTOR_REPLACEMENT),
    ("-schema-version", SELECTOR_REPLACEMENT),
    ("-schema-version 1", SELECTOR_REPLACEMENT),
    ("-schema-version 2", SELECTOR_REPLACEMENT),
    ("-schema-version 3", SELECTOR_REPLACEMENT),
)

# ---------------------------------------------------------------------------
# Old Skill instruction spellings and their canonical replacements
# ---------------------------------------------------------------------------
# Each entry is (old spelling, inventory row id, canonical replacement). The
# replacement text comes from the frozen inventory row and is the only
# supported spelling; the old form is never reinterpreted as a new command.
OLD_SKILL_INSTRUCTIONS: Final[tuple[tuple[str, str, str], ...]] = (
    ("--schema-version 1", "selector.schema-version.1", SELECTOR_REPLACEMENT),
    ("--schema-version 2", "selector.schema-version.2", SELECTOR_REPLACEMENT),
    ("--schema-version 3", "selector.schema-version.3", SELECTOR_REPLACEMENT),
    ("--synthesize", "docs.ref.synthesize", "research run without synthesis controls"),
    ("final_answer", "docs.ref.final_answer", "workflow evidence-only host-authored answer guidance"),
    ("cfg", "alias.top.cfg", "canonical V3 config namespace"),
    ("mdl", "alias.top.mdl", "canonical V3 provider.routes namespace"),
    ("model add", "command.nested_legacy.model.add", "provider.routes.add"),
    ("model current", "command.nested_legacy.model.current", "provider.routes.current"),
    ("model list", "command.nested_legacy.model.list", "provider.routes.list"),
    ("model remove", "command.nested_legacy.model.remove", "provider.routes.remove"),
    ("route-calibrate", "command.legacy_control.route-calibrate", "dev.route.calibrate"),
    ("diagnose", "command.legacy_control.diagnose", "dev.diagnose.openai-compatible"),
    ("regression", "command.legacy_control.regression", "dev.regression"),
    ("skills status", "command.nested_legacy.skills.status", "dev.skills.status"),
    ("skills update", "command.nested_legacy.skills.update", "dev.skills.update"),
)
