"""Narrow, versioned Python facade for Smart Search v2 Core operations.

This module is the supported Python integration surface. The broad v1
``smart_search.service`` facade is removed; this narrow typed facade is the
only public Python entrypoint for evidence operations.
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
