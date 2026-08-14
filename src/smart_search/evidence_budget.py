"""Shared evidence-output budget constants.

This dependency-light module lets the typed primitives and strict output
contracts enforce the same default fetched-evidence cap without creating an
owner-to-contract import dependency.
"""

from __future__ import annotations

# Default per-evidence content projection cap in Python characters. Normal V2
# fetch and Research use this bound; ``fetch --full`` remains untruncated.
DEFAULT_FETCH_CONTENT_LIMIT = 8000
