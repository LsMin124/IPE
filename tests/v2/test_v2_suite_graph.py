"""v2 test-suite 통합테스트 — with_test_suite=True (Phase 3 M4 step5).

verification 통과 후 generator_designer → input_generator → suite_assembler 가
풀 채점셋을 만들어 end_success 로 종료하는 배선을 mock LLM + scripted runner 로 검증:
1. success: 검증 통과 → contract 저작 → 결정론 입력 생성 → golden 으로 expected 채움.
2. verification fail: suite 노드 미진입 (test_suite/generator_contract None).
3. partial drop: golden 이 일부 입력 실행 실패 → 그 케이스만 drop, 나머지로 assembled
   (assembled/planned 비율 = 규약 정합 anchor 의 분자/분모 보존 확인).
4. build guard: synthesis 항상 배선(Phase 4) — with_test_suite 만으로도 golden 필수.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ipe.sandbox.runner import RunResult, RunSpec
from ipe.v1.schema import (
    AlgorithmDesign,
    BlueprintFormalization,
    ComplexityBound,
    ConstraintRange,
    EdgeCaseSpec,
    GeneratorContract,
    Invariant,
    InvariantViolation,
    IOContract,
    IOFieldSpec,
    IOSchema,
    NarrativeDraft,
    NarrativeFaithfulnessReport,
    ProblemSpec,
    SampleTestCase,
    ScaleFamily,
    SolutionAttempt,
    StrategySeed,
    TargetAlgorithm,
)
from ipe.v2.graph import build_v2_graph
from ipe.v2.state import V2State, initial_v2_state

_SAMPLE_INPUTS = ["i0", "i1", "i2"]


# ---------- modeling mocks ----------


class _FixedStrategistLLM:
    def seed(self, state: Any) -> StrategySeed:
        return StrategySeed(reduction_core=TargetAlgorithm.SORT, domain="logistics")


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
        return NarrativeDraft(scenario="물류 시나리오")


class _FaithfulLLM:
    def assess(self, state: Any) -> NarrativeFaithfulnessReport:
        return NarrativeFaithfulnessReport(faithful=True)


# ---------- synthesis mocks ----------


class _SpecBridgeLLM:
    """expected = f'{prefix}-{input}' 인 spec 저작. prefix='ans' 면 runner 와 일치."""

    def __init__(self, prefix: str = "ans") -> None:
        self._prefix = prefix

    def author(self, state: Any) -> ProblemSpec:
        return ProblemSpec(
            target_algorithm=TargetAlgorithm.SORT,
            title="t",
            description="placeholder",
            io_contract=IOContract(input_format="i", output_format="o"),
            sample_testcases=[
                SampleTestCase(input_text=i, expected_output=f"{self._prefix}-{i}")
                for i in _SAMPLE_INPUTS
            ],
        )


class _DesignerLLM:
    def generate(self, state: Any) -> AlgorithmDesign:
        return AlgorithmDesign(
            algorithm_name="sort",
            complexity_target=ComplexityBound(
                time_big_o="O(N log N)", space_big_o="O(N)"
            ),
            pseudocode="sort values.",
            invariants=[Invariant(kind="sorted", description="x")],
        )


class _CoderLLM:
    def __init__(self, code: str) -> None:
        self._code = code

    def generate(self, state: Any) -> SolutionAttempt:
        return SolutionAttempt(code=self._code, iteration=0)


# ---------- generator designer mock (M4) ----------


class _FixedGeneratorDesignerLLM:
    """결정론 검증용 contract — tier bound 를 단일값으로 고정해 입력을 예측가능하게.

    small×2(N=5..5) + large×1(N=9..9) + edge 'zero'(empty bias → 기본범위 하한 0)
    → 입력 ["5", "5", "9", "0"], planned 총 4.
    """

    def design(self, state: Any) -> GeneratorContract:
        return GeneratorContract(
            scale_families=(
                ScaleFamily(
                    name="small",
                    case_count=2,
                    field_bounds=(
                        ConstraintRange(name="N", min_value=5, max_value=5),
                    ),
                ),
                ScaleFamily(
                    name="large",
                    case_count=1,
                    field_bounds=(
                        ConstraintRange(name="N", min_value=9, max_value=9),
                    ),
                ),
            ),
            edge_cases=(EdgeCaseSpec(name="zero"),),
            determinism_seed=7,
        )


# ---------- scripted runner ----------


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


def _echo_answer(code: str, stdin: str) -> tuple[str, str]:
    return ("OK", f"ans-{stdin}")


def _fail_on_nine(code: str, stdin: str) -> tuple[str, str]:
    """생성 입력 '9'(large tier)만 실행 실패 — synthesis sample(i0..i2)은 무관."""
    if stdin.strip() == "9":
        return ("RTE", "")
    return _echo_answer(code, stdin)


def _final(raw: Any) -> V2State:
    return raw if isinstance(raw, V2State) else V2State.model_validate(raw)


def _suite_graph(
    *,
    spec_prefix: str = "ans",
    runner_fn: Callable[[str, str], tuple[str, str]] = _echo_answer,
    verifier_getter: Any = None,
) -> Any:
    return build_v2_graph(
        strategist_llm=_FixedStrategistLLM(),
        formalizer_llm=_FixedFormalizerLLM(),
        narrative_llm=_FixedNarrativeLLM(),
        faithfulness_llm=_FaithfulLLM(),
        spec_bridge_llm=_SpecBridgeLLM(spec_prefix),
        designer_llm=_DesignerLLM(),
        golden_llms=[_CoderLLM("# G0"), _CoderLLM("# G1")],
        brute_llm=_CoderLLM("# B"),
        golden_origins=["opus", "sonnet"],
        runner=_MarkerRunner(runner_fn),
        verifier_getter=(
            verifier_getter if verifier_getter is not None else (lambda _a: None)
        ),
        with_test_suite=True,
        generator_designer_llm=_FixedGeneratorDesignerLLM(),
    )


class _ViolatingVerifier:
    """symbolic verifier mock — invariant violation 검출 (verification fail 유발)."""

    def verify(self, **_kw: Any) -> list[InvariantViolation]:
        return [
            InvariantViolation(invariant_kind="non_negative", description="음수 거리")
        ]

    def count_engaged_samples(self, spec: Any) -> int:
        return len(spec.sample_testcases)


def _run(graph: Any, run_id: str) -> V2State:
    return _final(
        graph.invoke(
            initial_v2_state(run_id, TargetAlgorithm.SORT),
            config={"recursion_limit": 50},
        )
    )


# ---------- 1. success: 검증 통과 → 풀 채점셋 assembled ----------


def test_suite_pipeline_success() -> None:
    graph = _suite_graph()
    final = _run(graph, "run-suite-ok")

    assert final.final_status == "success"
    # 상류 아티팩트 (synthesis 까지 기존과 동일)
    assert final.verification is not None and final.verification.overall_pass is True
    assert len(final.candidates) == 3  # suite 노드 full-state 재emit 에도 dedup 유지
    # M4 아티팩트
    assert final.generator_contract is not None
    assert final.generator_contract.total_planned_cases == 4
    suite = final.test_suite
    assert suite is not None
    assert suite.is_assembled is True
    assert suite.golden_origin == "opus"  # reconciliation.adopted_origin provenance
    assert [c.input_text for c in suite.cases] == ["5", "5", "9", "0"]
    assert [c.category for c in suite.cases] == ["small", "small", "large", "zero"]
    assert [c.expected_output for c in suite.cases] == [
        "ans-5",
        "ans-5",
        "ans-9",
        "ans-0",
    ]


# ---------- 2. verification fail → suite 미진입 ----------


def test_suite_skipped_on_verification_fail() -> None:
    # sample 은 sample_filler 가 golden 으로 채워 통과 — verification fail 은 symbolic
    # invariant violation 으로 유발 (sample mismatch 는 sample_filler 가 흡수).
    graph = _suite_graph(verifier_getter=lambda _a: _ViolatingVerifier())
    final = _run(graph, "run-suite-vfail")

    assert final.final_status == "fail_verification"
    assert final.generator_contract is None  # generator_designer 미실행
    assert final.test_suite is None  # suite 노드 미진입


# ---------- 3. partial drop: 실행 실패 입력만 drop ----------


def test_suite_partial_drop_keeps_rest() -> None:
    graph = _suite_graph(runner_fn=_fail_on_nine)
    final = _run(graph, "run-suite-drop")

    assert final.final_status == "success"
    assert final.generator_contract is not None
    assert final.generator_contract.total_planned_cases == 4  # anchor 분모 보존
    suite = final.test_suite
    assert suite is not None
    assert suite.is_assembled is True
    assert len(suite.cases) == 3  # '9'(large) 만 drop
    assert [c.category for c in suite.cases] == ["small", "small", "zero"]


# ---------- 4. build guard ----------


def test_test_suite_still_requires_golden() -> None:
    # synthesis 항상 배선(Phase 4) — with_test_suite 만으로도 golden/brute 필수
    # (suite→synthesis 체인이 golden 을 요구. 옛 with_test_suite⇒with_synthesis 가드 대체).
    with pytest.raises(ValueError, match="golden_llms"):
        build_v2_graph(with_test_suite=True)
