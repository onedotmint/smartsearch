"""Narrow, versioned Python facade for Smart Search v2 Core operations.

This module is the supported Python integration surface for Phase 3. It is not
re-exported from smart_search.service and must not expand that facade.
"""

from __future__ import annotations

from .canonical_operations import (
    ContentFetchRequest,
    DocsDiscoveryRequest,
    SiteDiscoveryRequest,
    SourceDiscoveryRequest,
    capability_status,
    composite_search as _composite_search,
    content_fetch,
    docs_discovery,
    site_discovery,
    source_discovery,
)
from .v2_contract import V2Envelope

__all__ = [
    "ContentFetchRequest",
    "DocsDiscoveryRequest",
    "SiteDiscoveryRequest",
    "SourceDiscoveryRequest",
    "V2Envelope",
    "capability_status",
    "content_fetch",
    "docs_discovery",
    "site_discovery",
    "source_discovery",
]
