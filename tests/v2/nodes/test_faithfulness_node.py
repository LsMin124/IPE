"""Faithfulness 노드 단위 테스트 (Phase 3 M3 step4, round-trip).

- ``make_faithfulness_node``: narrative + blueprint → NarrativeFaithfulnessReport
  (state.faithfulness). 정보 은닉=OK(faithful), 정보 왜곡=reject(faithful=False).
- precondition: narrative + blueprint 둘 다 필요.

mock LLM 으로 sandbox/네트워크 없이 결정론 검증.
"""

from __future__ import annotations

import pytest

from ipe.v1.schema import (
    ConstraintRange,
    GraphShape,
    IOFieldSpec,
    IOSchema,
    Narrative,
    NarrativeFaithfulnessReport,
    OutputInvariant,
    ProblemBlueprint,
    TargetAlgorithm,
)
from ipe.v2.nodes import make_faithfulness_node
from ipe.v2.state import V2State, initial_v2_state


def _blueprint() -> ProblemBlueprint:
    return ProblemBlueprint(
        reduction_core=TargetAlgorithm.DIJKSTRA,
        domain="logistics",
        io_schema=IOSchema(
            inputs=(IOFieldSpec(name="N", type="int"),),
            output_type="int",
            output_format="단일 정수 (최단거리)",
        ),
        output_invariants=(
            OutputInvariant(kind="non_negative", description="거리는 음수 불가"),
        ),
    )


def _narrative(hidden: bool = True) -> Narrative:
    return Narrative(
        scenario="물류 센터에서 거점 간 최소 이동 비용을 구하라 ...",
        hidden=hidden,
        domain="logistics",
    )


def _state(*, with_narrative: bool = True, with_blueprint: bool = True) -> V2State:
    base = initial_v2_state("run-v2", TargetAlgorithm.DIJKSTRA)
    update: dict[str, object] = {}
    if with_blueprint:
        update["blueprint"] = _blueprint()
    if with_narrative:
        update["narrative"] = _narrative()
    return base.model_copy(update=update)


class _FixedFaithfulnessLLM:
    """고정 report 를 반환하는 mock (state 무시)."""

    def __init__(self, report: NarrativeFaithfulnessReport) -> None:
        self._report = report

    def assess(self, state: V2State) -> NarrativeFaithfulnessReport:
        return self._report


def test_faithfulness_populates_report_when_faithful() -> None:
    report = NarrativeFaithfulnessReport(faithful=True)
    out = make_faithfulness_node(_FixedFaithfulnessLLM(report))(_state())

    assert out.faithfulness is not None
    assert out.faithfulness.faithful is True
    assert out.faithfulness.distortions == ()


def test_faithfulness_records_distortions_when_unfaithful() -> None:
    report = NarrativeFaithfulnessReport(
        faithful=False,
        distortions=("지문은 '최댓값'을 요구하나 schema 출력은 최단거리",),
    )
    out = make_faithfulness_node(_FixedFaithfulnessLLM(report))(_state())

    assert out.faithfulness is not None
    assert out.faithfulness.faithful is False
    assert len(out.faithfulness.distortions) == 1


def test_faithfulness_requires_narrative() -> None:
    bare = _state(with_narrative=False)  # blueprint 만 있음
    node = make_faithfulness_node(
        _FixedFaithfulnessLLM(NarrativeFaithfulnessReport(faithful=True))
    )
    with pytest.raises(ValueError, match="narrative"):
        node(bare)


def test_faithfulness_requires_blueprint() -> None:
    bare = _state(with_blueprint=False)  # narrative 만 있음
    node = make_faithfulness_node(
        _FixedFaithfulnessLLM(NarrativeFaithfulnessReport(faithful=True))
    )
    with pytest.raises(ValueError, match="blueprint"):
        node(bare)


def test_faithfulness_preserves_original_state() -> None:
    state = _state()
    out = make_faithfulness_node(
        _FixedFaithfulnessLLM(NarrativeFaithfulnessReport(faithful=True))
    )(state)
    assert state.faithfulness is None  # 원본 불변
    assert out.faithfulness is not None
    assert out.narrative is state.narrative  # narrative/blueprint 보존
    assert out.blueprint is state.blueprint


# ---------- Phase 1b: 구조 사실 머신체크 ----------


def _graph_state() -> V2State:
    """directed 핀된 graph blueprint + narrative — faithfulness 가 구조사실 수령."""
    bp = ProblemBlueprint(
        reduction_core=TargetAlgorithm.DIJKSTRA,
        domain="logistics",
        io_schema=IOSchema(
            inputs=(
                IOFieldSpec(
                    name="edges",
                    type="weighted_edges",
                    size_range=ConstraintRange(name="V", min_value=2, max_value=100),
                    graph_shape=GraphShape(directed=True),
                ),
            ),
            output_type="int",
            output_format="단일 정수",
        ),
    )
    base = initial_v2_state("run-v2", TargetAlgorithm.DIJKSTRA)
    return base.model_copy(update={"blueprint": bp, "narrative": _narrative()})


def test_faithfulness_prompt_flags_structural_fact_contradiction() -> None:
    """Phase 1b: '구조 사실'(directed/self-loop 등)과 narrative 모순 = distortion 규율."""
    from ipe.v2.nodes.faithfulness import _SYSTEM_PROMPT

    assert "구조 사실" in _SYSTEM_PROMPT
    assert "directed" in _SYSTEM_PROMPT or "단방향" in _SYSTEM_PROMPT


def test_faithfulness_user_prompt_includes_structural_facts() -> None:
    """graph_shape 핀된 필드면 user prompt 에 구조 사실 DATA 주입 (narrative 와 비교)."""
    from ipe.v2.nodes.faithfulness import _build_user_prompt

    prompt = _build_user_prompt(_graph_state())
    assert "구조 사실" in prompt
    assert "단방향" in prompt  # directed=True 투영
