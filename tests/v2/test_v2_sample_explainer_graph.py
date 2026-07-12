"""sample_explainer 그래프 배선 통합테스트 (W2B) — 예제 설명 opt-in 플래그.

full 파이프라인(mock LLM + scripted runner, test_v2_synthesis_graph 패턴 미러)에서:
1. ``with_sample_explanations=True`` + mock explainer 주입 → 최종 state 의 spec
   sample description 이 채워지고 expected(sample_filler 산출)는 보존 — 즉
   sample_filler → sample_explainer → edge_filler 순서 배선이 실효.
2. 기본(False) → sample_explainer 미배선, description 은 기존처럼 빈 문자열
   (기존 경로 무회귀).
3. explainer LLM 예외 → 무변경 통과로 파이프라인은 여전히 success (장식적 품질
   채널 — 실패 클래스 불증가).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ipe.sandbox.runner import RunResult, RunSpec
from ipe.v1.schema import (
    AlgorithmDesign,
    BlueprintFormalization,
    ComplexityBound,
    Invariant,
    IOFieldSpec,
    IOSchema,
    NarrativeDraft,
    NarrativeFaithfulnessReport,
    SampleExplanations,
    SolutionAttempt,
    StrategySeed,
    TargetAlgorithm,
)
from ipe.v2.graph import build_v2_graph
from ipe.v2.state import V2State, initial_v2_state

# ---------- modeling / synthesis mocks (test_v2_synthesis_graph 미러) ----------


class _FixedStrategistLLM:
    def seed(self, state: Any) -> StrategySeed:
        return StrategySeed(reduction_core=TargetAlgorithm.DIJKSTRA, domain="logistics")


class _FixedFormalizerLLM:
    def formalize(self, state: Any) -> BlueprintFormalization:
        return BlueprintFormalization(
            io_schema=IOSchema(
                inputs=(IOFieldSpec(name="N", type="int"),),
                output_type="int",
                output_format="단일 정수",
            )
        )


class _FixedNarrativeLLM:
    def render(self, state: Any, *, hidden: bool) -> NarrativeDraft:
        return NarrativeDraft(title="물류 경로", scenario="물류 시나리오")


class _FaithfulLLM:
    def assess(self, state: Any) -> NarrativeFaithfulnessReport:
        return NarrativeFaithfulnessReport(faithful=True)


class _DesignerLLM:
    def generate(self, state: Any) -> AlgorithmDesign:
        return AlgorithmDesign(
            algorithm_name="dijkstra",
            complexity_target=ComplexityBound(
                time_big_o="O(E log V)", space_big_o="O(V)"
            ),
            pseudocode="relax edges.",
            invariants=[Invariant(kind="non_negative", description="x")],
        )


class _CoderLLM:
    def __init__(self, code: str) -> None:
        self._code = code

    def generate(self, state: Any) -> SolutionAttempt:
        return SolutionAttempt(code=self._code, iteration=0)


class _MarkerRunner:
    def __init__(self, fn: Callable[[str, str], tuple[str, str]]) -> None:
        self._fn = fn

    def run(self, spec: RunSpec) -> RunResult:
        py = sorted(Path(spec.cwd).glob("*.py"))
        code = py[0].read_text(encoding="utf-8") if py else ""
        status, stdout = self._fn(code, spec.stdin)
        return RunResult(
            status=status,  # type: ignore[arg-type]
            returncode=0 if status == "OK" else 1,
            stdout=stdout,
            stderr="" if status == "OK" else "boom",
            elapsed_ms=1,
        )


def _agreeing(code: str, stdin: str) -> tuple[str, str]:
    return ("OK", f"ans-{stdin}")


# ---------- explainer mocks ----------


class _StateAwareExplainerLLM:
    """샘플 개수에 맞춰 결정론 설명 생성 — spec_bridge 가 만드는 샘플 수에 무관."""

    def explain(self, state: V2State) -> SampleExplanations:
        spec = state.spec
        assert spec is not None
        return SampleExplanations(
            explanations=[
                f"설명-{i}" for i in range(len(spec.sample_testcases))
            ]
        )


class _RaisingExplainerLLM:
    def explain(self, state: V2State) -> SampleExplanations:
        msg = "LLM boom"
        raise RuntimeError(msg)


# ---------- helpers ----------


def _final(raw: Any) -> V2State:
    return raw if isinstance(raw, V2State) else V2State.model_validate(raw)


def _graph(**kwargs: Any) -> Any:
    return build_v2_graph(
        composition_mode="single",
        strategist_llm=_FixedStrategistLLM(),
        formalizer_llm=_FixedFormalizerLLM(),
        narrative_llm=_FixedNarrativeLLM(),
        faithfulness_llm=_FaithfulLLM(),
        designer_llm=_DesignerLLM(),
        golden_llms=[_CoderLLM("# G0"), _CoderLLM("# G1")],
        brute_llm=_CoderLLM("# B"),
        golden_origins=["opus", "sonnet"],
        runner=_MarkerRunner(_agreeing),
        verifier_getter=lambda _a: None,
        **kwargs,
    )


def _run(graph: Any, run_id: str) -> V2State:
    return _final(
        graph.invoke(
            initial_v2_state(run_id, TargetAlgorithm.DIJKSTRA),
            config={"recursion_limit": 50},
        )
    )


# ---------- 1. 활성 배선: description 이 최종 state 에 반영 ----------


def test_with_sample_explanations_fills_descriptions() -> None:
    graph = _graph(
        with_sample_explanations=True,
        sample_explainer_llm=_StateAwareExplainerLLM(),
    )
    final = _run(graph, "run-expl-on")

    assert final.final_status == "success"
    assert final.spec is not None
    samples = final.spec.sample_testcases
    # sample_explainer 가 sample_filler 뒤에 실행 — description 채움 + expected 보존
    assert [s.description for s in samples] == [
        f"설명-{i}" for i in range(len(samples))
    ]
    assert all(s.expected_output for s in samples)  # sample_filler 산출 보존


# ---------- 2. 기본(off): 기존 경로 무회귀 ----------


def test_default_off_keeps_descriptions_empty() -> None:
    graph = _graph()  # with_sample_explanations 기본 False
    final = _run(graph, "run-expl-off")

    assert final.final_status == "success"
    assert final.spec is not None
    assert all(s.description == "" for s in final.spec.sample_testcases)
    assert all(s.expected_output for s in final.spec.sample_testcases)


# ---------- 3. explainer 예외: 파이프라인 생존 (실패 클래스 불증가) ----------


def test_explainer_failure_does_not_break_pipeline() -> None:
    graph = _graph(
        with_sample_explanations=True,
        sample_explainer_llm=_RaisingExplainerLLM(),
    )
    final = _run(graph, "run-expl-boom")

    assert final.final_status == "success"  # 무변경 통과 — 검증 경로 계속
    assert final.spec is not None
    assert all(s.description == "" for s in final.spec.sample_testcases)
