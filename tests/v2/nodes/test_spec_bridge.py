"""spec_bridge 노드 단위 테스트 (Phase 4 — 순수 투영, LLM 없음).

``make_spec_bridge_node``: blueprint + narrative + io_schema → ProblemSpec (state.spec).
모든 필드가 IR 의 함수 — target_algorithm=blueprint.reduction_core,
title=narrative.title, description=narrative.scenario, constraints/io_contract/parser/
samples=io_schema 코드 투영. LLM 저작 없음(Opus 호출 강등, fail_spec_authoring 제거).
"""

from __future__ import annotations

import pytest

from ipe.v1.schema import (
    ConstraintRange,
    IOFieldSpec,
    IOSchema,
    Narrative,
    OutputInvariant,
    ProblemBlueprint,
    ProblemSpec,
    TargetAlgorithm,
)
from ipe.v2.generation.input_gen import (
    derive_degenerate_inputs,
    render_input_format,
)
from ipe.v2.generation.input_parser import render_input_parser
from ipe.v2.nodes import make_spec_bridge_node
from ipe.v2.state import V2State, initial_v2_state


def _blueprint() -> ProblemBlueprint:
    return ProblemBlueprint(
        reduction_core=TargetAlgorithm.DIJKSTRA,
        domain="logistics",
        io_schema=IOSchema(
            inputs=(IOFieldSpec(name="N", type="int"),),
            output_type="int",
            output_format="단일 정수",
        ),
    )


def _narrative() -> Narrative:
    return Narrative(
        title="배송 센터 경로 비용",
        scenario="물류 센터 최소 이동 비용 지문",
        hidden=True,
        domain="logistics",
    )


def _state(*, with_blueprint: bool = True, with_narrative: bool = True) -> V2State:
    base = initial_v2_state("run-v2", TargetAlgorithm.DIJKSTRA)
    update: dict[str, object] = {}
    if with_blueprint:
        update["blueprint"] = _blueprint()
    if with_narrative:
        update["narrative"] = _narrative()
    return base.model_copy(update=update)


def test_spec_bridge_populates_spec() -> None:
    out = make_spec_bridge_node()(_state())

    spec = out.spec
    assert isinstance(spec, ProblemSpec)
    assert len(spec.sample_testcases) == 3


def test_spec_bridge_title_from_narrative() -> None:
    """title 은 narrative author 저작(creative slot 1) — 순수 carry-over (Phase 4)."""
    out = make_spec_bridge_node()(_state())

    spec = out.spec
    assert isinstance(spec, ProblemSpec)
    assert spec.title == "배송 센터 경로 비용"  # narrative.title


def test_spec_bridge_generates_samples_deterministically_empties_expected() -> None:
    """input_text 는 io_schema 에서 결정적 생성(형식 항상 정합 → 골든 파서 IndexError
    차단). expected 는 비우고 하류 sample_filler 가 golden 으로 채운다. 같은 run_id →
    같은 샘플(재현)."""
    from ipe.v2.nodes.spec_bridge import _SAMPLE_COUNT, _generate_sample_inputs

    out = make_spec_bridge_node()(_state())

    spec = out.spec
    assert isinstance(spec, ProblemSpec)
    # io_schema 에서 결정적 생성 (run_id 'run-v2')
    expected_inputs = _generate_sample_inputs(_blueprint().io_schema, "run-v2")
    assert [s.input_text for s in spec.sample_testcases] == expected_inputs
    assert len(spec.sample_testcases) == _SAMPLE_COUNT
    # 단일 int 필드 → 각 샘플은 정수 한 줄 (형식 정합)
    assert all(s.input_text.strip().lstrip("-").isdigit() for s in spec.sample_testcases)
    assert all(s.expected_output == "" for s in spec.sample_testcases)  # expected 비움


def test_spec_bridge_carry_over_target_algorithm_and_description() -> None:
    """target_algorithm=blueprint.reduction_core, description=narrative.scenario."""
    out = make_spec_bridge_node()(_state())

    spec = out.spec
    assert isinstance(spec, ProblemSpec)
    assert spec.target_algorithm is TargetAlgorithm.DIJKSTRA  # blueprint.reduction_core
    assert spec.description == "물류 센터 최소 이동 비용 지문"  # narrative.scenario


def test_spec_bridge_freezes_io_contract_to_canonical_render() -> None:
    """io_contract: input_format=canonical 렌더, output_format=io_schema carry-over
    (step6: 직렬화 규약↔골든 파서 정렬, ratio 0.0 해소)."""
    out = make_spec_bridge_node()(_state())

    spec = out.spec
    assert isinstance(spec, ProblemSpec)
    assert spec.io_contract.input_format == render_input_format(_blueprint().io_schema)
    assert spec.io_contract.output_format == "단일 정수"  # io_schema.output_format


def test_spec_bridge_freezes_input_parser_code_from_io_schema() -> None:
    """stdin 파서도 io_schema 에서 render_input_parser 로 파생 — synthesis 코더가
    파서 분산(IndexError·중복카운트) 없이 알고리즘만 작성."""
    out = make_spec_bridge_node()(_state())

    spec = out.spec
    assert isinstance(spec, ProblemSpec)
    assert spec.input_parser_code == render_input_parser(_blueprint().io_schema)
    assert spec.input_parser_code  # 비어있지 않음 (v2 는 항상 주입)


def test_spec_bridge_requires_blueprint() -> None:
    node = make_spec_bridge_node()
    with pytest.raises(ValueError, match="blueprint"):
        node(_state(with_blueprint=False))


def test_spec_bridge_requires_narrative() -> None:
    node = make_spec_bridge_node()
    with pytest.raises(ValueError, match="narrative"):
        node(_state(with_narrative=False))


# ---------- 경계 샘플 1개 보장 (edge_case_semantics invariant → 마지막 샘플 교체) ----------


def _sized_io_schema() -> IOSchema:
    """sized 필드 포함 io_schema — derive_degenerate_inputs 가 'min' 퇴화를 파생."""
    return IOSchema(
        inputs=(
            IOFieldSpec(
                name="A",
                type="int_array",
                size_range=ConstraintRange(name="N", min_value=1, max_value=10),
                value_range=ConstraintRange(name="A_i", min_value=1, max_value=100),
            ),
        ),
        output_type="int",
        output_format="단일 정수",
    )


def _edge_invariant() -> OutputInvariant:
    return OutputInvariant(
        kind="edge_case_semantics", description="해 없음이면 -1 출력"
    )


def test_generate_sample_inputs_replaces_last_with_first_degenerate() -> None:
    """edge_case_semantics 류 invariant + 퇴화 파생 가능 schema → 마지막 샘플이
    derive_degenerate_inputs 의 첫 퇴화 입력으로 교체 (개수 불변, BOJ 표준)."""
    from ipe.v2.nodes.spec_bridge import _SAMPLE_COUNT, _generate_sample_inputs

    schema = _sized_io_schema()
    texts = _generate_sample_inputs(
        schema, "run-v2", output_invariants=(_edge_invariant(),)
    )
    degenerate = derive_degenerate_inputs(schema)
    assert degenerate  # 전제: sized 필드로 'min' 퇴화 파생
    assert len(texts) == _SAMPLE_COUNT  # 개수 불변
    assert texts[-1] == degenerate[0][1]  # 마지막 샘플 = 첫 퇴화 입력


def test_generate_sample_inputs_unchanged_without_edge_invariant() -> None:
    """invariant 없으면 기존 동작 그대로 (bias=random 3개, 회귀 가드)."""
    from ipe.v2.nodes.spec_bridge import _generate_sample_inputs

    schema = _sized_io_schema()
    plain = _generate_sample_inputs(schema, "run-v2")
    explicit = _generate_sample_inputs(schema, "run-v2", output_invariants=())
    assert plain == explicit
    degenerate = derive_degenerate_inputs(schema)
    assert plain[-1] != degenerate[0][1]  # 교체 미발생 (seed 상 구분되는 입력)


def test_generate_sample_inputs_unchanged_when_no_degenerate() -> None:
    """invariant 가 있어도 퇴화 파생이 비면(스칼라 only) 교체 없음 — 순수 no-op."""
    from ipe.v2.nodes.spec_bridge import _generate_sample_inputs

    scalar_schema = _blueprint().io_schema  # N int only → 퇴화 파생 빈 튜플
    assert derive_degenerate_inputs(scalar_schema) == ()
    with_inv = _generate_sample_inputs(
        scalar_schema, "run-v2", output_invariants=(_edge_invariant(),)
    )
    without = _generate_sample_inputs(scalar_schema, "run-v2")
    assert with_inv == without


def test_spec_bridge_node_sample_includes_degenerate_case() -> None:
    """node 레벨: blueprint.output_invariants 의 edge_case_semantics 가 샘플에
    반영 — 지문의 퇴화 처방('도달 불가 → -1' 류)을 샘플이 실제 시연."""
    bp = ProblemBlueprint(
        reduction_core=TargetAlgorithm.SORT,
        domain="logistics",
        io_schema=_sized_io_schema(),
        output_invariants=(_edge_invariant(),),
    )
    state = _state().model_copy(update={"blueprint": bp})
    out = make_spec_bridge_node()(state)

    spec = out.spec
    assert isinstance(spec, ProblemSpec)
    assert len(spec.sample_testcases) == 3  # 개수 불변
    degenerate = derive_degenerate_inputs(bp.io_schema)
    assert spec.sample_testcases[-1].input_text == degenerate[0][1]
    # expected 는 여전히 비움 — sample_filler 가 golden 실행으로 채움 (순수성 유지)
    assert all(s.expected_output == "" for s in spec.sample_testcases)


def test_spec_bridge_preserves_original_state() -> None:
    state = _state()
    out = make_spec_bridge_node()(state)
    assert state.spec is None  # 원본 불변
    assert out.spec is not None
    assert out.blueprint is state.blueprint
    assert out.narrative is state.narrative
