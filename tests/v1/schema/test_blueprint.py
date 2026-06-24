"""Blueprint-first 아티팩트 단위 테스트 (Phase 3 M3 step1).

ProblemBlueprint(frozen formal 계약) + Narrative(late) + NarrativeFaithfulnessReport
(round-trip). frozen + extra=forbid + 기본값 + 검증 규칙.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ipe.v1.schema import (
    BlueprintFormalization,
    ConstraintRange,
    GraphShape,
    IOFieldSpec,
    IOSchema,
    Narrative,
    NarrativeDraft,
    NarrativeFaithfulnessReport,
    OutputInvariant,
    ProblemBlueprint,
    StrategySeed,
    TargetAlgorithm,
)


def _io_schema() -> IOSchema:
    return IOSchema(
        inputs=(
            IOFieldSpec(
                name="N",
                type="int",
                value_range=ConstraintRange(name="N", min_value=1, max_value=100000),
            ),
            IOFieldSpec(
                name="edges",
                type="weighted_edges",
                size_range=ConstraintRange(name="E", min_value=0, max_value=200000),
            ),
        ),
        output_type="int",
        output_format="단일 정수 (최단거리, 도달불가 시 -1)",
    )


def _blueprint() -> ProblemBlueprint:
    return ProblemBlueprint(
        reduction_core=TargetAlgorithm.DIJKSTRA,
        domain="logistics",
        io_schema=_io_schema(),
        output_invariants=(
            OutputInvariant(kind="non_negative", description="거리는 음수 불가"),
        ),
    )


# ---------- ProblemBlueprint ----------


def test_blueprint_minimal_construction_and_defaults() -> None:
    bp = _blueprint()
    assert bp.reduction_core is TargetAlgorithm.DIJKSTRA
    assert bp.composition == ()  # default
    assert bp.domain == "logistics"
    assert bp.io_schema.output_type == "int"
    assert len(bp.output_invariants) == 1


def test_blueprint_composition_records_synthesis_techniques() -> None:
    bp = ProblemBlueprint(
        reduction_core=TargetAlgorithm.DIJKSTRA,
        composition=(TargetAlgorithm.BINARY_SEARCH, TargetAlgorithm.UNION_FIND),
        domain="network",
        io_schema=_io_schema(),
    )
    assert bp.composition == (
        TargetAlgorithm.BINARY_SEARCH,
        TargetAlgorithm.UNION_FIND,
    )


def test_blueprint_is_frozen() -> None:
    bp = _blueprint()
    with pytest.raises(ValidationError):
        bp.domain = "other"  # type: ignore[misc]


def test_blueprint_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ProblemBlueprint(
            reduction_core=TargetAlgorithm.DIJKSTRA,
            domain="x",
            io_schema=_io_schema(),
            unexpected="nope",  # type: ignore[call-arg]
        )


def test_blueprint_requires_domain() -> None:
    with pytest.raises(ValidationError):
        ProblemBlueprint(
            reduction_core=TargetAlgorithm.DIJKSTRA,
            domain="",  # min_length=1
            io_schema=_io_schema(),
        )


# ---------- IOSchema / IOFieldSpec ----------


def test_io_schema_requires_at_least_one_input() -> None:
    with pytest.raises(ValidationError):
        IOSchema(inputs=(), output_type="int", output_format="x")


def test_io_field_optional_ranges_default_none() -> None:
    f = IOFieldSpec(name="s", type="string")
    assert f.size_range is None
    assert f.value_range is None
    assert f.graph_shape is None  # graph_shape 옵셔널 (Phase 1)
    assert f.description == ""


def test_io_schema_indexing_default_one() -> None:
    schema = IOSchema(
        inputs=(IOFieldSpec(name="s", type="int"),),
        output_type="int",
        output_format="x",
    )
    assert schema.indexing == 1  # F9 기본 1-indexed


# ---------- GraphShape (Phase 1 F6~F8) ----------


def test_graph_shape_requires_directed() -> None:
    """directed 는 F8 잠재 모순 핀 — 기본값 없이 필수."""
    with pytest.raises(ValidationError):
        GraphShape()  # type: ignore[call-arg]


def test_graph_shape_defaults_equal_serializer_constants() -> None:
    g = GraphShape(directed=True)
    assert g.self_loops is False  # 현 직렬화기 상수
    assert g.multi_edges is True
    assert g.connectivity == "maybe_disconnected"


def test_graph_shape_is_frozen_and_forbids_extra() -> None:
    g = GraphShape(directed=False)
    with pytest.raises(ValidationError):
        g.directed = True  # type: ignore[misc]
    with pytest.raises(ValidationError):
        GraphShape(directed=True, unexpected="nope")  # type: ignore[call-arg]


def test_io_field_carries_graph_shape() -> None:
    f = IOFieldSpec(
        name="edges",
        type="weighted_edges",
        graph_shape=GraphShape(directed=False, self_loops=True),
    )
    assert f.graph_shape is not None
    assert f.graph_shape.directed is False
    assert f.graph_shape.self_loops is True


def test_io_schema_indexing_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        IOSchema(
            inputs=(IOFieldSpec(name="s", type="int"),),
            output_type="int",
            output_format="x",
            indexing=2,  # Literal[0, 1] 만 허용
        )


# ---------- Narrative ----------


def test_narrative_hidden_and_direct() -> None:
    hidden = Narrative(scenario="물류 센터 ...", hidden=True, domain="logistics")
    direct = Narrative(scenario="다익스트라를 구현하라", hidden=False, domain="algo")
    assert hidden.hidden is True
    assert direct.hidden is False


def test_narrative_requires_scenario() -> None:
    with pytest.raises(ValidationError):
        Narrative(scenario="", hidden=True, domain="d")


# ---------- NarrativeFaithfulnessReport ----------


def test_faithfulness_report_defaults_no_distortions() -> None:
    r = NarrativeFaithfulnessReport(faithful=True)
    assert r.faithful is True
    assert r.distortions == ()


def test_faithfulness_report_records_distortions() -> None:
    r = NarrativeFaithfulnessReport(
        faithful=False,
        distortions=("출력이 최댓값이라 기술됐으나 schema 는 최단거리",),
    )
    assert r.faithful is False
    assert len(r.distortions) == 1


# ---------- StrategySeed (M3 step2) ----------


def test_strategy_seed_minimal_and_defaults() -> None:
    seed = StrategySeed(reduction_core=TargetAlgorithm.DIJKSTRA, domain="logistics")
    assert seed.reduction_core is TargetAlgorithm.DIJKSTRA
    assert seed.composition == ()  # default
    assert seed.domain == "logistics"
    assert seed.rationale == ""  # default


def test_strategy_seed_records_composition_and_rationale() -> None:
    seed = StrategySeed(
        reduction_core=TargetAlgorithm.KNAPSACK,
        composition=(TargetAlgorithm.BINARY_SEARCH,),
        domain="warehouse",
        rationale="용량 배분을 예산 최적화로 위장",
    )
    assert seed.composition == (TargetAlgorithm.BINARY_SEARCH,)
    assert seed.rationale.startswith("용량")


def test_strategy_seed_requires_domain() -> None:
    with pytest.raises(ValidationError):
        StrategySeed(reduction_core=TargetAlgorithm.BFS, domain="")  # min_length=1


def test_strategy_seed_is_frozen() -> None:
    seed = StrategySeed(reduction_core=TargetAlgorithm.BFS, domain="d")
    with pytest.raises(ValidationError):
        seed.domain = "other"  # type: ignore[misc]


def test_strategy_seed_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        StrategySeed(
            reduction_core=TargetAlgorithm.BFS,
            domain="d",
            unexpected="nope",  # type: ignore[call-arg]
        )


# ---------- BlueprintFormalization (M3 step2) ----------


def test_blueprint_formalization_carries_formal_face_only() -> None:
    f = BlueprintFormalization(io_schema=_io_schema())
    assert f.io_schema.output_type == "int"
    assert f.output_invariants == ()  # default
    # 알고리즘 결정 필드는 존재하지 않음 (Formalizer 가 못 바꿈 — freeze 규율)
    assert not hasattr(f, "reduction_core")


def test_blueprint_formalization_records_invariants() -> None:
    f = BlueprintFormalization(
        io_schema=_io_schema(),
        output_invariants=(
            OutputInvariant(kind="non_negative", description="거리는 음수 불가"),
        ),
    )
    assert f.output_invariants[0].kind == "non_negative"


def test_blueprint_formalization_is_frozen_and_forbids_extra() -> None:
    f = BlueprintFormalization(io_schema=_io_schema())
    with pytest.raises(ValidationError):
        f.io_schema = _io_schema()  # type: ignore[misc]
    with pytest.raises(ValidationError):
        BlueprintFormalization(
            io_schema=_io_schema(),
            reduction_core=TargetAlgorithm.DIJKSTRA,  # type: ignore[call-arg]
        )


# ---------- NarrativeDraft (M3 step3) ----------


def test_narrative_draft_carries_scenario_only() -> None:
    d = NarrativeDraft(scenario="물류 센터 시나리오 ...")
    assert d.scenario.startswith("물류")
    # hidden/domain 은 노드가 스탬프 — draft 에는 없음 (Author 가 못 바꿈)
    assert not hasattr(d, "hidden")
    assert not hasattr(d, "domain")


def test_narrative_draft_requires_scenario() -> None:
    with pytest.raises(ValidationError):
        NarrativeDraft(scenario="")  # min_length=1


def test_narrative_draft_is_frozen_and_forbids_extra() -> None:
    d = NarrativeDraft(scenario="s")
    with pytest.raises(ValidationError):
        d.scenario = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        NarrativeDraft(scenario="s", hidden=True)  # type: ignore[call-arg]
