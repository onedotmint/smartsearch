"""Local-only provider namespace catalog.

This module is loaded only after a successful v1 parse. It projects one
capability-status snapshot into a redacted, non-probing catalog and does not
create provider clients.
"""

from __future__ import annotations

from typing import Any

from .capability_service import get_capability_status, provider_profiles
from .capability_taxonomy import list_provider_qualifications, map_v1_to_v2_capability
from .cli_constants import COMMAND_ALIASES, MODEL_COMMAND_ALIASES

_REPLACEMENTS = {
    "exa": "search (V2 source/docs discovery)",
    "context7": "search (V2 docs_discovery)",
    "zhipu": "search (V2 source_discovery)",
    "zhipu-mcp": "search | fetch (V2 source_discovery / content_fetch)",
    "zhipu-mcp-zread": "no public command surface",
    "anysearch": "no public command surface",
    "xai-responses": "provider routes",
    "openai-compatible": "provider routes",
    "tavily": "search | fetch | map",
    "firecrawl": "search | fetch",
    "jina": "fetch",
    "zhipu-mcp-reader": "fetch",
}

_LEGACY_COMMANDS: dict[str, list[str]] = {}


def _legacy_aliases(provider: str) -> list[str]:
    commands = list(_LEGACY_COMMANDS.get(provider, []))
    if provider in {"xai-responses", "openai-compatible"}:
        return [
            *COMMAND_ALIASES["model"],
            *MODEL_COMMAND_ALIASES["current"],
            *MODEL_COMMAND_ALIASES["list"],
            *MODEL_COMMAND_ALIASES["add"],
            *MODEL_COMMAND_ALIASES["remove"],
        ]
    aliases: list[str] = []
    for command in commands:
        aliases.extend(COMMAND_ALIASES.get(command, []))
    return aliases


def _qualification_metadata(provider: str) -> tuple[str, str, list[dict[str, Any]]]:
    qualifications = list_provider_qualifications(provider=provider)
    tiers = [str(item.get("tier") or "") for item in qualifications]
    stability = [str(item.get("stability") or "") for item in qualifications]
    if provider in {"anysearch", "zhipu-mcp-zread"}:
        return "experimental", "experimental", qualifications
    return (tiers[0] if tiers else "advanced", stability[0] if stability else "legacy", qualifications)


def provider_catalog(*, include_status: bool) -> dict[str, Any]:
    """Build one local catalog from exactly one capability-status snapshot."""
    status = get_capability_status()
    profiles = provider_profiles()
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for capability, capability_state in status.items():
        for item in capability_state.get("provider_status") or []:
            state = dict(item)
            state["capability"] = capability
            by_provider.setdefault(str(item.get("provider") or ""), []).append(state)

    providers: list[dict[str, Any]] = []
    for provider in sorted(profiles):
        # ``main-search`` is a historical synthesis profile rather than a
        # registered Provider. Catalog entries must represent only Providers.
        if provider not in by_provider:
            continue
        profile = profiles[provider]
        capabilities = list(profile.get("capabilities") or [profile.get("capability", "")])
        capabilities = [capability for capability in capabilities if capability]
        v2_capabilities = [
            v2_capability
            for capability in capabilities
            if (v2_capability := map_v1_to_v2_capability(capability)) is not None
        ]
        tier, stability, qualifications = _qualification_metadata(provider)
        entry: dict[str, Any] = {
            "provider": provider,
            "capabilities": capabilities,
            "v2_capabilities": v2_capabilities,
            "tier": tier,
            "stability": stability,
            "replacement": _REPLACEMENTS.get(provider, "provider status"),
            "network_behavior": "network_on_explicit_command",
            "legacy_commands": list(_LEGACY_COMMANDS.get(provider, [])),
            "legacy_aliases": _legacy_aliases(provider),
            "qualifications": qualifications,
        }
        if include_status:
            state_by_capability = {str(item.get("capability") or ""): item for item in by_provider.get(provider, [])}
            entry["status"] = [
                {
                    "capability": capability,
                    "configured": bool(state_by_capability.get(capability, {}).get("configured")),
                    "enabled": bool(state_by_capability.get(capability, {}).get("enabled")),
                    "eligible": bool(state_by_capability.get(capability, {}).get("eligible")),
                    "reason": str(state_by_capability.get(capability, {}).get("reason") or "not_registered"),
                }
                for capability in capabilities
            ]
        providers.append(entry)
    return {
        "ok": True,
        "local_only": True,
        "network_behavior": "no_provider_requests_or_probes",
        "providers": providers,
    }
