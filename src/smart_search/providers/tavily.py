"""Tavily raw-result normalizer for the v1 retrieval core.

This module performs no network I/O. The callable accepts the provider's
original ``results`` list shape so captured responses can be replayed offline.
"""

from __future__ import annotations

from ..core.normalizers import normalize_tavily as to_discovery_candidates
