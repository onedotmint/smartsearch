from __future__ import annotations

import copy

import pytest
from jsonschema import Draft202012Validator

from smart_search.research_plan import (
    PLAN_EXECUTABLE_OPERATION_IDS,
    RESEARCH_PLAN_JSON_SCHEMA,
    RESEARCH_PLAN_SCHEMA_VERSION,
    ResearchPlan,
    ResearchPlanError,
    ResearchPlanOperation,
    build_research_plan,
    serialize_research_plan,
    validate_research_plan_dict,
)
from smart_search.research_plan_render import (
    LEGACY_PLAN_PROJECTION_VERSION,
    build_projection_context,
    projection_entry,
    render_v1_steps,
)
from smart_search.v2_contract import V2_META_OPERATION_CAPABILITY_STATUS


def _op(op_id: str, operation: str = "source_discovery", **kwargs) -> ResearchPlanOperation:
    return ResearchPlanOperation(
        id=op_id,
        operation=operation,
        input=kwargs.get("input", {"query": "q"}),
        constraints=kwargs.get("constraints", {}),
        depends_on=kwargs.get("depends_on", ()),
    )


def test_schema_and_round_trip():
    Draft202012Validator.check_schema(RESEARCH_PLAN_JSON_SCHEMA)
    plan = build_research_plan(
        [
            _op("discover-primary"),
            _op(
                "fetch-selected",
                "content_fetch",
                input={"candidate_refs": ["discover-primary"]},
                constraints={"max_items": 3},
                depends_on=("discover-primary",),
            ),
        ]
    )
    payload = serialize_research_plan(plan)
    assert payload["schema_version"] == RESEARCH_PLAN_SCHEMA_VERSION
    assert "command" not in payload
    assert "output_path" not in payload
    for item in payload["operations"]:
        assert set(item) == {"id", "operation", "input", "constraints", "depends_on"}
        assert "command" not in item
        assert "output_path" not in item
    Draft202012Validator(RESEARCH_PLAN_JSON_SCHEMA).validate(payload)
    assert validate_research_plan_dict(payload) == payload


def test_rejects_duplicate_unknown_self_and_meta_operations():
    with pytest.raises(ResearchPlanError):
        build_research_plan([_op("a"), _op("a")])
    with pytest.raises(ResearchPlanError):
        build_research_plan([_op("a", depends_on=("missing",))])
    with pytest.raises(ResearchPlanError):
        # later dependency is forbidden (must be earlier)
        build_research_plan([
            _op("a", depends_on=("b",)),
            _op("b"),
        ])
    with pytest.raises(ResearchPlanError):
        ResearchPlanOperation(id="a", operation="source_discovery", depends_on=("a",), input={"query": "q"})
    with pytest.raises(ResearchPlanError):
        ResearchPlanOperation(
            id="a",
            operation=V2_META_OPERATION_CAPABILITY_STATUS,
            input={},
        )
    with pytest.raises(ResearchPlanError):
        ResearchPlanOperation(id="a", operation="answer_synthesis", input={"query": "q"})
    with pytest.raises(ResearchPlanError):
        ResearchPlanOperation(
            id="a",
            operation="source_discovery",
            input={"query": "q", "command": "smart-search search"},
        )
    with pytest.raises(ResearchPlanError):
        ResearchPlanOperation(
            id="a",
            operation="source_discovery",
            constraints={"selection": {"provider": "tavily"}},
        )
    with pytest.raises(ResearchPlanError):
        ResearchPlanOperation(
            id="a",
            operation="source_discovery",
            input={"query": "q", "provider_id": "tavily"},
        )


@pytest.mark.parametrize("field", ("input", "constraints", "depends_on"))
def test_raw_plan_rejects_null_required_operation_containers(field):
    raw = {
        "schema_version": "2",
        "operations": [{
            "id": "discover-primary",
            "operation": "source_discovery",
            "input": {"query": "q"},
            "constraints": {},
            "depends_on": [],
        }],
    }
    raw["operations"][0][field] = None
    with pytest.raises(ResearchPlanError):
        validate_research_plan_dict(raw)


def test_projection_is_one_to_one_and_renders_v1_steps():
    discover = _op("discover-primary", input={"query": "bitcoin"})
    fetch = ResearchPlanOperation(
        id="fetch-selected",
        operation="content_fetch",
        input={"candidate_refs": ["discover-primary"]},
        constraints={"max_items": 3},
        depends_on=("discover-primary",),
    )
    plan = build_research_plan([discover, fetch])
    projection = build_projection_context(
        "C:/tmp/evidence",
        [
            projection_entry(
                discover,
                renderer_kind="search",
                purpose="broad discovery",
                subquestion_id="sq1",
                args={"query": "bitcoin", "extra_sources": 3},
                output_suffix="01-search.json",
            ),
            projection_entry(
                fetch,
                renderer_kind="fetch",
                purpose="fetch selected",
                subquestion_id="sq1",
                args={"url": "<key-url>"},
                output_suffix="02-fetch.md",
            ),
        ],
    )
    assert projection.version == LEGACY_PLAN_PROJECTION_VERSION
    steps = render_v1_steps(plan, projection)
    assert [step["id"] for step in steps] == ["s1", "s2"]
    assert steps[0]["tool"] == "search"
    assert steps[1]["tool"] == "fetch"
    assert steps[0]["output_path"].endswith("01-search.json")
    assert steps[0]["output_path"] in steps[0]["command"]
    assert "smart-search search" in steps[0]["command"]
    # structured plan fixture is independent of renderer
    frozen = serialize_research_plan(plan)
    other = copy.deepcopy(frozen)
    assert other == frozen


def test_orphaned_or_missing_projection_is_rejected():
    plan = build_research_plan([_op("a")])
    with pytest.raises(ResearchPlanError):
        render_v1_steps(
            plan,
            build_projection_context(
                "/tmp/e",
                [
                    projection_entry(
                        _op("b"),
                        renderer_kind="search",
                        purpose="x",
                        subquestion_id="sq1",
                        output_suffix="01-search.json",
                    )
                ],
            ),
        )


def test_phase3_plan_operations_exclude_synthesis_and_meta():
    assert "answer_synthesis" not in PLAN_EXECUTABLE_OPERATION_IDS
    assert V2_META_OPERATION_CAPABILITY_STATUS not in PLAN_EXECUTABLE_OPERATION_IDS
