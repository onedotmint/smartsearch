"""Frozen JSON baseline guards for the V2 and Research Workflow envelopes.

Every golden string in ``tests/fixtures/json_compat_baselines.py`` is a
byte-wise snapshot of current serializer output. This test rebuilds each
representative outcome through the real projection + serialization + CLI emit
path and requires byte equality, so a field removal, reorder, type change, or
silent shape drift fails without a deliberate fixture update in the same
commit. Additive optional fields are compatible and must also update the
fixtures.
"""

from __future__ import annotations

import json

import pytest

from smart_search.canonical_operations import _project_evidence_outcome
from smart_search.research_workflow_contract import serialize_workflow, validate_workflow_dict
from smart_search.v2_contract import serialize_result, validate_envelope_dict
from tests.fixtures.json_compat_baselines import (
    GOLDEN_BUILDERS,
    V2_DEGRADED_FETCH_GOLDEN,
    V2_FETCH_GOLDEN,
    V2_SEARCH_GOLDEN,
    WORKFLOW_COMPLETE_GOLDEN,
    WORKFLOW_DEGRADED_GOLDEN,
)


def _emit(payload: dict) -> str:
    """Exact CLI JSON emit format (``ensure_ascii=False, indent=2``)."""
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _validate_golden(golden: str, family: str) -> None:
    """The frozen golden must itself satisfy the strict schema."""
    raw = json.loads(golden)
    if family == "v2":
        validate_envelope_dict(raw)
    else:
        validate_workflow_dict(raw)


V2_GOLDENS = {
    "v2-search": V2_SEARCH_GOLDEN,
    "v2-fetch": V2_FETCH_GOLDEN,
    "v2-degraded-fetch": V2_DEGRADED_FETCH_GOLDEN,
}

WORKFLOW_GOLDENS = {
    "workflow-complete": WORKFLOW_COMPLETE_GOLDEN,
    "workflow-degraded": WORKFLOW_DEGRADED_GOLDEN,
}


@pytest.mark.parametrize("name", sorted(V2_GOLDENS))
def test_v2_envelope_matches_frozen_golden(name):
    builder, family = GOLDEN_BUILDERS[name]
    assert family == "v2"
    payload = serialize_result(_project_evidence_outcome(builder()))
    assert _emit(payload) == V2_GOLDENS[name]
    _validate_golden(V2_GOLDENS[name], "v2")


@pytest.mark.parametrize("name", sorted(WORKFLOW_GOLDENS))
def test_workflow_envelope_matches_frozen_golden(name):
    builder, family = GOLDEN_BUILDERS[name]
    assert family == "workflow"
    payload = serialize_workflow(builder())
    assert _emit(payload) == WORKFLOW_GOLDENS[name]
    _validate_golden(WORKFLOW_GOLDENS[name], "workflow")


def test_golden_truncation_metadata_is_present():
    """The fetch golden freezes the evidence budget metadata contract."""
    item = json.loads(V2_FETCH_GOLDEN)["evidence"]["items"][0]
    assert item["truncated"] is True
    assert item["original_length"] == 8000
    assert item["returned_length"] == 64
    assert set(item) == {
        "id", "resource", "provider", "title", "content",
        "truncated", "original_length", "returned_length",
    }


def test_golden_builders_are_all_frozen():
    """Every declared golden has a frozen string; none are left empty."""
    for name, (builder, family) in GOLDEN_BUILDERS.items():
        if family == "v2":
            assert V2_GOLDENS[name], name
        else:
            assert WORKFLOW_GOLDENS[name], name
