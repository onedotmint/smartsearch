"""Renderer-kind tool mapping for typed Research Plan operations.

The v1 shell-command projection (``render_v1_steps``, projection entries and
output paths) is removed with the legacy deep-plan surface; only the
renderer-kind -> tool mapping used by the offline planner remains.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

RENDERER_KIND_TO_TOOL: Mapping[str, str] = MappingProxyType(
    {
        "search": "search",
        "fetch": "fetch",
        "map": "map",
    }
)

__all__ = ["RENDERER_KIND_TO_TOOL"]
