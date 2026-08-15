"""Shared evidence-output budget constants.

This dependency-light module lets the typed primitives and strict output
contracts enforce the same default fetched-evidence cap without creating an
owner-to-contract import dependency.
"""

from __future__ import annotations

# Default per-evidence content projection cap in Python characters. Normal V2
# fetch and Research use this bound; ``fetch --full`` remains untruncated.
DEFAULT_FETCH_CONTENT_LIMIT = 8000

# Transport-level hard cap on bytes read from one provider response body on
# the fetch path. Protects memory: the body is streamed and this bound stops
# oversized or decompressed responses from being fully buffered before the
# evidence projection (which is a separate output-only cap). Applies to every
# fetch path including ``fetch --full``; exceeding it is a classified
# ``too_large`` provider failure, never a silent truncation.
DEFAULT_FETCH_TRANSPORT_LIMIT = 5 * 1024 * 1024
