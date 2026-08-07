"""Stable public service facade for the split service workflows."""

from .capability_service import (
    capabilities,
    get_capability_status,
    intent_router_status,
    provider_profiles,
    route,
    route_calibrate,
    validate_command_capabilities,
    validate_minimum_profile,
)
from .config import config
from .operations_service import (
    config_list,
    config_path,
    config_set,
    config_unset,
    current_model,
    diagnose_openai_compatible,
    doctor,
    model_add,
    model_list,
    model_remove,
    smoke,
    write_output,
)
from .provider_fetch_commands import fetch, map_site
from .research_service import build_deep_research_plan, research
from .search_service import extra_results_to_sources, fetch_available_models, get_available_models_cached, search
from .service_support import DEEP_ALLOWED_TOOLS, RESEARCH_ROUTE_POLICY_VERSION


__all__ = [
    "build_deep_research_plan",
    "capabilities",
    "config",
    "config_list",
    "config_path",
    "config_set",
    "config_unset",
    "current_model",
    "diagnose_openai_compatible",
    "doctor",
    "extra_results_to_sources",
    "fetch",
    "fetch_available_models",
    "get_available_models_cached",
    "get_capability_status",
    "intent_router_status",
    "map_site",
    "model_add",
    "model_list",
    "model_remove",
    "provider_profiles",
    "research",
    "route",
    "route_calibrate",
    "search",
    "smoke",
    "validate_command_capabilities",
    "validate_minimum_profile",
    "write_output",
    "DEEP_ALLOWED_TOOLS",
    "RESEARCH_ROUTE_POLICY_VERSION",
]
