"""generator_designer 노드 단위 테스트 (Phase 3 — 순수 투영).

``make_generator_designer_node``: frozen blueprint.io_schema → GeneratorContract
(state.generator_contract). LLM 없음 — ``derive_generator_contract`` 로 결정론 파생.
계약 내용(scale tiers·실현가능 edge)의 정밀 검증은 ``test_input_gen`` 의 derive 테스트가
담당; 여기선 노드 계약(블루프린트 요구·state 보존·파생 호출)에 집중한다.
"""

from __future__ import annotations

import pytest

from ipe.v1.schema import (
    ConstraintRange,
    GeneratorContract,
    IOFieldSpec,
    IOSchema,
    ProblemBlueprint,
    TargetAlgorithm,
)
from ipe.v2.nodes import make_generator_designer_node
from ipe.v2.state import V2State, initial_v2_state


def _blueprint(io_schema: IOSchema) -> ProblemBlueprint:
    return ProblemBlueprint(
        reduction_core=TargetAlgorithm.DIJKSTRA,
        domain="logistics",
        io_schema=io_schema,
    )


def _scalar_schema() -> IOSchema:
    return IOSchema(
        inputs=(
            IOFieldSpec(
                name="N",
                type="int",
                value_range=ConstraintRange(name="N", min_value=1, max_value=100000),
            ),
        ),
        output_type="int",
        output_format="단일 정수",
    )


def _graph_schema() -> IOSchema:
    return IOSchema(
        inputs=(
            IOFieldSpec(
                name="edges",
                type="weighted_edges",
                size_range=ConstraintRange(name="V", min_value=2, max_value=1000),
                value_range=ConstraintRange(name="w", min_value=1, max_value=100),
            ),
        ),
        output_type="int",
        output_format="단일 정수",
    )


def _state(io_schema: IOSchema | None) -> V2State:
    base = initial_v2_state("run-v2", TargetAlgorithm.DIJKSTRA)
    if io_schema is None:
        return base
    return base.model_copy(update={"blueprint": _blueprint(io_schema)})


def test_generator_designer_projects_scalar_schema() -> None:
    """sized 필드 없는 스칼라 schema → 단일 nominal family, edge 없음."""
    out = make_generator_designer_node()(_state(_scalar_schema()))

    contract = out.generator_contract
    assert isinstance(contract, GeneratorContract)
    assert [f.name for f in contract.scale_families] == ["nominal"]
    assert contract.edge_cases == ()  # 스칼라엔 실현 가능한 퇴화 없음


def test_generator_designer_projects_graph_schema() -> None:
    """대규모 graph schema → small/large scale tier + 실현가능 edge(min/max/empty/
    disconnected) + adversarial 아키타입(max_stress·구조·가중치 — 상용 저지 케이스 강도)."""
    out = make_generator_designer_node()(_state(_graph_schema()))

    contract = out.generator_contract
    assert isinstance(contract, GeneratorContract)
    assert {f.name for f in contract.scale_families} == {"small", "large"}
    assert {e.name for e in contract.edge_cases} == {
        "min_size",
        "max_size",
        "empty",
        "disconnected",
        "max_stress",
        "path_chain",
        "star",
        "dense",
        "cycle_heavy",
        "duplicate_edges",
        "equal_weights",
        "extreme_weights",
    }
    # boundary/퇴화 4 + max_stress 1 + 아키타입 7 = edge 12, tier 5 → 17 케이스 계획
    assert contract.total_planned_cases == 17


def test_generator_designer_requires_blueprint() -> None:
    node = make_generator_designer_node()
    with pytest.raises(ValueError, match="blueprint"):
        node(_state(None))


def test_generator_designer_preserves_original_state() -> None:
    state = _state(_graph_schema())
    out = make_generator_designer_node()(state)
    assert state.generator_contract is None  # 원본 불변
    assert out.generator_contract is not None
    assert out.blueprint is state.blueprint  # 다른 채널 보존
