"""v2 CLI(main_v2) 단위 테스트 (Phase 4 — P1/P2 2-모드 수렴).

mock LLM 으로 build_v2_graph 한 **full 파이프라인** 그래프를 main(graph=...) 에 주입 →
실 LLM/네트워크 없이 CLI plumbing(--mode 파싱 / summary / exit code) 결정론 검증.
그래프 주입 경로라 모드 노브(hidden/composition/qa_kinds)는 print·pad 만 좌우한다
(실 노브 배선은 graph/strategist 레벨 테스트가 담당).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, get_args

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
    IOContract,
    IOFieldSpec,
    IOSchema,
    NarrativeDraft,
    NarrativeFaithfulnessReport,
    ProblemSpec,
    QAReview,
    QAReviewerKind,
    SampleTestCase,
    ScaleFamily,
    SolutionAttempt,
    StrategySeed,
    TargetAlgorithm,
)
from ipe.v2.graph import build_v2_graph
from ipe.v2.main_v2 import main

_SYNTH_INPUTS = ["i0", "i1", "i2"]
_ALL_QA_KINDS: tuple[QAReviewerKind, ...] = get_args(QAReviewerKind)


# ---------- modeling mocks ----------


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
        return NarrativeDraft(scenario="물류 시나리오 지문")


class _ScriptedFaithfulnessLLM:
    def __init__(self, faithful_seq: list[bool]) -> None:
        self._seq = list(faithful_seq)
        self.calls = 0

    def assess(self, state: Any) -> NarrativeFaithfulnessReport:
        val = self._seq[min(self.calls, len(self._seq) - 1)]
        self.calls += 1
        return NarrativeFaithfulnessReport(
            faithful=val, distortions=() if val else ("왜곡 근거",)
        )


# ---------- synthesis / suite / qa mocks ----------


class _SpecBridgeLLM:
    """expected = f'ans-{input}' 인 spec 저작 — _MarkerRunner 와 일치."""

    def author(self, state: Any) -> ProblemSpec:
        return ProblemSpec(
            target_algorithm=TargetAlgorithm.DIJKSTRA,
            title="hidden-problem",
            description="placeholder",
            io_contract=IOContract(input_format="i", output_format="o"),
            sample_testcases=[
                SampleTestCase(input_text=i, expected_output=f"ans-{i}")
                for i in _SYNTH_INPUTS
            ],
        )


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
    """code 의 marker 로 결정론적 결과 — 'WRONG' 면 불일치, 그 외 'ans-{stdin}'."""

    def run(self, spec: RunSpec) -> RunResult:
        py = sorted(Path(spec.cwd).glob("*.py"))
        code = py[0].read_text(encoding="utf-8") if py else ""
        stdout = f"wrong-{spec.stdin}" if "WRONG" in code else f"ans-{spec.stdin}"
        return RunResult(
            status="OK", returncode=0, stdout=stdout, stderr="", elapsed_ms=1
        )


class _FixedGeneratorDesignerLLM:
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
            ),
            edge_cases=(EdgeCaseSpec(name="zero"),),
            determinism_seed=7,
        )


class _QAReviewerLLM:
    def __init__(self, passed: bool, kind: QAReviewerKind) -> None:
        self._passed = passed
        self._kind = kind

    def review(self, state: Any, *, kind: QAReviewerKind) -> QAReview:
        return QAReview(kind=self._kind, passed=self._passed, rationale="scripted")


def _full_graph(
    *,
    faithful_seq: list[bool] | None = None,
    fail_kinds: tuple[QAReviewerKind, ...] = (),
    golden_codes: list[str] | None = None,
) -> Any:
    """full 파이프라인(modeling+synthesis+suite+qa) mock 그래프 — CLI plumbing 검증용."""
    qa_llms: dict[QAReviewerKind, Any] = {
        kind: _QAReviewerLLM(kind not in fail_kinds, kind) for kind in _ALL_QA_KINDS
    }
    return build_v2_graph(
        strategist_llm=_FixedStrategistLLM(),
        formalizer_llm=_FixedFormalizerLLM(),
        narrative_llm=_FixedNarrativeLLM(),
        faithfulness_llm=_ScriptedFaithfulnessLLM(faithful_seq or [True]),
        spec_bridge_llm=_SpecBridgeLLM(),
        designer_llm=_DesignerLLM(),
        golden_llms=[_CoderLLM(c) for c in (golden_codes or ["# G0", "# G1"])],
        brute_llm=_CoderLLM("# B"),
        golden_origins=["opus", "sonnet"],
        runner=_MarkerRunner(),
        verifier_getter=lambda _a: None,
        with_test_suite=True,
        generator_designer_llm=_FixedGeneratorDesignerLLM(),
        with_qa=True,
        qa_reviewer_llms=qa_llms,
    )


# ---------- success: p2 (기본) full 파이프라인 ----------


def test_main_p2_success_prints_full_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--algorithm", "dijkstra", "--mode", "p2"], graph=_full_graph())
    out = capsys.readouterr().out
    assert code == 0
    assert "mode=p2" in out
    assert "hidden=True" in out
    assert "composition=composed" in out
    assert "final_status=success" in out
    assert "reduction_core=dijkstra" in out
    # synthesis/suite/qa 요약 노출
    assert "target_algorithm=dijkstra" in out
    assert "candidates: 3" in out  # golden×2 + brute
    assert "test_suite:" in out
    assert "qa: overall_pass=True" in out


# ---------- p1 모드: 단일·공개 노브가 print 에 반영 ----------


def test_main_p1_mode_prints_single_public(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--algorithm", "dijkstra", "--mode", "p1"], graph=_full_graph())
    out = capsys.readouterr().out
    assert code == 0
    assert "mode=p1" in out
    assert "hidden=False" in out  # P1 = 공개
    assert "composition=single" in out
    # P1 qa_kinds 는 leakage 제외 3종
    assert "qa_kinds=['ambiguity', 'fairness', 'difficulty']" in out


def test_main_default_mode_is_p2(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--algorithm", "dijkstra"], graph=_full_graph())
    out = capsys.readouterr().out
    assert code == 0
    assert "mode=p2" in out


# ---------- 실패 경로 ----------


def test_main_faithfulness_failure_returns_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        ["--algorithm", "dijkstra", "--mode", "p2", "--max-iter", "2"],
        graph=_full_graph(faithful_seq=[False]),
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "final_status=fail_faithfulness" in out


def test_main_synthesis_rejected_returns_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """golden 불일치 → exit 1 + fail_synthesis_rejected (증거 ref/cand 출력 노출)."""
    code = main(
        ["--algorithm", "dijkstra"],
        graph=_full_graph(golden_codes=["# G0", "# WRONG G1"]),
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "final_status=fail_synthesis_rejected" in out
    assert "all_agree=False" in out
    assert "ans-" in out
    assert "wrong-" in out


def test_main_qa_failure_returns_one_and_prints_failed_kinds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """QA blocker → exit 1 + failed_kinds 가시화 (채점셋은 완성)."""
    code = main(
        ["--algorithm", "dijkstra"],
        graph=_full_graph(fail_kinds=("leakage",)),
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "final_status=fail_qa" in out
    assert "qa: overall_pass=False" in out
    assert "failed_kinds=['leakage']" in out


# ---------- 부수 plumbing ----------


def test_main_verbose_prints_scenario(capsys: pytest.CaptureFixture[str]) -> None:
    main(["--algorithm", "dijkstra", "--verbose"], graph=_full_graph())
    out = capsys.readouterr().out
    assert "VERBOSE" in out
    assert "물류 시나리오 지문" in out


def test_main_unsupported_algorithm_exits() -> None:
    with pytest.raises(SystemExit):
        main(["--algorithm", "no_such_algo"], graph=_full_graph())
