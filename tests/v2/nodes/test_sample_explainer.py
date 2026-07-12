"""sample_explainer 노드 단위 테스트 (W2B) — 완성 샘플에 BOJ '예제 설명' 저작.

mock LLM 주입으로 결정론 검증 (sample_filler 테스트 패턴 미러): ① 설명이 샘플
순서대로 description 에 채워지고 input/expected 는 불변, ② 개수 불일치면 min
길이까지만 채우고 나머지 원본 유지(길이 보존), ③ **LLM 예외 시 state 무변경**
(장식적 품질 채널 — 파이프라인 생존 우선, 기존 노드들의 raise 정책과 다른 의도적
선택), ④ spec/narrative 부재 시 방어적 no-op (LLM 미호출).
"""

from __future__ import annotations

from ipe.v1.schema import (
    IOContract,
    Narrative,
    ProblemSpec,
    SampleExplanations,
    SampleTestCase,
    TargetAlgorithm,
)
from ipe.v2.nodes import make_sample_explainer_node
from ipe.v2.state import V2State, initial_v2_state


class _FixedExplainerLLM:
    """고정 explanations 반환 + 호출 기록 (no-op 경로의 미호출 검증용)."""

    def __init__(self, explanations: list[str]) -> None:
        self._explanations = explanations
        self.calls = 0

    def explain(self, state: V2State) -> SampleExplanations:
        self.calls += 1
        return SampleExplanations(explanations=self._explanations)


class _RaisingExplainerLLM:
    """LLM 장애 시뮬레이션 — 예외 시 무변경 통과 검증용."""

    def explain(self, state: V2State) -> SampleExplanations:
        msg = "LLM boom"
        raise RuntimeError(msg)


def _spec() -> ProblemSpec:
    return ProblemSpec(
        target_algorithm=TargetAlgorithm.DIJKSTRA,
        title="창고 배송 비용",
        description="은닉 지문",
        io_contract=IOContract(input_format="N", output_format="정수"),
        sample_testcases=[
            SampleTestCase(input_text="3", expected_output="7"),
            SampleTestCase(input_text="5", expected_output="-1"),
            SampleTestCase(input_text="7", expected_output="0"),
        ],
    )


def _state(
    *, spec: ProblemSpec | None, with_narrative: bool = True
) -> V2State:
    base = initial_v2_state("run", TargetAlgorithm.DIJKSTRA)
    update: dict[str, object] = {}
    if spec is not None:
        update["spec"] = spec
    if with_narrative:
        update["narrative"] = Narrative(
            title="창고 배송 비용",
            scenario="창고에서 상점으로 물품을 배송한다.",
            hidden=True,
            domain="logistics",
        )
    return base.model_copy(update=update)


def test_fills_descriptions_in_sample_order() -> None:
    """설명이 샘플 순서 그대로 description 에 채워진다 — input/expected 불변."""
    llm = _FixedExplainerLLM(["비용 7이 최소다.", "도달 불가라 -1.", "같은 지점이라 0."])
    out = make_sample_explainer_node(llm)(_state(spec=_spec()))

    filled = out.spec
    assert filled is not None
    assert [s.description for s in filled.sample_testcases] == [
        "비용 7이 최소다.",
        "도달 불가라 -1.",
        "같은 지점이라 0.",
    ]
    # 설명 외 필드는 불변
    assert [s.input_text for s in filled.sample_testcases] == ["3", "5", "7"]
    assert [s.expected_output for s in filled.sample_testcases] == ["7", "-1", "0"]


def test_count_mismatch_fills_min_and_keeps_rest() -> None:
    """explanations 가 샘플보다 적으면 min 길이까지만 채우고 나머지 원본 유지 —
    길이 보존 (ProblemSpec min 3 제약 안전)."""
    llm = _FixedExplainerLLM(["첫 설명.", "둘째 설명."])
    out = make_sample_explainer_node(llm)(_state(spec=_spec()))

    filled = out.spec
    assert filled is not None
    assert len(filled.sample_testcases) == 3  # 길이 보존
    assert [s.description for s in filled.sample_testcases] == [
        "첫 설명.",
        "둘째 설명.",
        "",  # 미채움 — 원본 유지
    ]


def test_count_overflow_ignores_extras() -> None:
    """explanations 가 샘플보다 많으면 샘플 개수까지만 사용 (초과분 무시)."""
    llm = _FixedExplainerLLM(["a", "b", "c", "잉여 설명"])
    out = make_sample_explainer_node(llm)(_state(spec=_spec()))

    filled = out.spec
    assert filled is not None
    assert [s.description for s in filled.sample_testcases] == ["a", "b", "c"]


def test_llm_exception_returns_state_unchanged() -> None:
    """LLM 예외 시 state 무변경 반환 — 예제 설명은 장식적 품질 채널이라
    파이프라인을 죽이지 않는다 (raise 하는 기존 노드들과 다른 의도적 선택)."""
    state = _state(spec=_spec())
    out = make_sample_explainer_node(_RaisingExplainerLLM())(state)

    assert out is state  # 무변경 (같은 객체)
    assert out.spec is not None
    assert all(s.description == "" for s in out.spec.sample_testcases)


def test_noop_without_spec() -> None:
    """spec 부재(상류 미완) — 방어적 no-op, LLM 미호출."""
    llm = _FixedExplainerLLM(["x"])
    state = _state(spec=None)
    out = make_sample_explainer_node(llm)(state)

    assert out is state
    assert llm.calls == 0


def test_noop_without_narrative() -> None:
    """narrative 부재 — 방어적 no-op (도메인 용어 컨텍스트 없이 저작하지 않는다)."""
    llm = _FixedExplainerLLM(["x", "y", "z"])
    state = _state(spec=_spec(), with_narrative=False)
    out = make_sample_explainer_node(llm)(state)

    assert out is state
    assert llm.calls == 0
