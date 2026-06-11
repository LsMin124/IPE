"""v2 CLI(main_v2) 단위 테스트 (Phase 3 M3 follow-up).

mock LLM 으로 build_v2_graph 한 그래프를 main(graph=...) 에 주입 → 실 LLM/네트워크
없이 CLI plumbing(arg parse / summary / exit code) 결정론 검증.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ipe.sandbox.runner import RunResult, RunSpec
from ipe.v1.schema import (
    AlgorithmDesign,
    BlueprintFormalization,
    ComplexityBound,
    Invariant,
    IOContract,
    IOFieldSpec,
    IOSchema,
    NarrativeDraft,
    NarrativeFaithfulnessReport,
    ProblemSpec,
    SampleTestCase,
    SolutionAttempt,
    StrategySeed,
    TargetAlgorithm,
)
from ipe.v2.graph import build_v2_graph
from ipe.v2.main_v2 import main


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


def _mock_graph(*, faithful_seq: list[bool], hidden: bool = True) -> Any:
    return build_v2_graph(
        strategist_llm=_FixedStrategistLLM(),
        formalizer_llm=_FixedFormalizerLLM(),
        narrative_llm=_FixedNarrativeLLM(),
        faithfulness_llm=_ScriptedFaithfulnessLLM(faithful_seq),
        hidden=hidden,
    )


# ---------- synthesis-path mocks (--with-synthesis) ----------

_SYNTH_INPUTS = ["i0", "i1", "i2"]


class _SpecBridgeLLM:
    """expected = f'ans-{input}' 인 spec 저작 — _by_marker runner 와 일치."""

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


def _mock_full_graph(*, golden_codes: list[str]) -> Any:
    """faithful 통과 + golden fan-out + executor 까지 mock 한 full-synthesis 그래프."""
    return build_v2_graph(
        strategist_llm=_FixedStrategistLLM(),
        formalizer_llm=_FixedFormalizerLLM(),
        narrative_llm=_FixedNarrativeLLM(),
        faithfulness_llm=_ScriptedFaithfulnessLLM([True]),
        with_synthesis=True,
        spec_bridge_llm=_SpecBridgeLLM(),
        designer_llm=_DesignerLLM(),
        golden_llms=[_CoderLLM(c) for c in golden_codes],
        brute_llm=_CoderLLM("# B"),
        golden_origins=["opus", "sonnet"],
        runner=_MarkerRunner(),
        verifier_getter=lambda _a: None,
    )


def test_main_success_returns_zero_and_prints_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        ["--algorithm", "dijkstra"], graph=_mock_graph(faithful_seq=[True])
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "final_status=success" in out
    assert "reduction_core=dijkstra" in out
    assert "faithful=True" in out


def test_main_failure_returns_one(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["--algorithm", "dijkstra", "--max-iter", "2"],
        graph=_mock_graph(faithful_seq=[False]),
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "final_status=fail_faithfulness" in out


def test_main_verbose_prints_scenario(capsys: pytest.CaptureFixture[str]) -> None:
    main(
        ["--algorithm", "dijkstra", "--verbose"],
        graph=_mock_graph(faithful_seq=[True]),
    )
    out = capsys.readouterr().out
    assert "VERBOSE" in out
    assert "물류 시나리오 지문" in out


def test_main_direct_flag_accepted(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["--algorithm", "dijkstra", "--direct"],
        graph=_mock_graph(faithful_seq=[True]),
    )
    assert code == 0
    assert "hidden=False" in capsys.readouterr().out


def test_main_unsupported_algorithm_exits() -> None:
    with pytest.raises(SystemExit):
        main(["--algorithm", "no_such_algo"], graph=_mock_graph(faithful_seq=[True]))


def test_main_with_synthesis_success_prints_synthesis_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--with-synthesis: golden 합의 + executor pass → exit 0 + synthesis 요약."""
    code = main(
        ["--algorithm", "dijkstra", "--with-synthesis"],
        graph=_mock_full_graph(golden_codes=["# G0", "# G1"]),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "final_status=success" in out
    # synthesis 아티팩트 요약 노출
    assert "spec:" in out
    assert "target_algorithm=dijkstra" in out
    assert "candidates: 3" in out  # golden×2 + brute, dedup reducer
    assert "all_agree=True" in out
    assert "adopted_origin=opus" in out  # reference golden_0
    assert "overall_pass=True" in out


def test_main_with_synthesis_rejected_returns_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--with-synthesis: golden 불일치 → exit 1 + fail_synthesis_rejected."""
    code = main(
        ["--algorithm", "dijkstra", "--with-synthesis"],
        graph=_mock_full_graph(golden_codes=["# G0", "# WRONG G1"]),
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "final_status=fail_synthesis_rejected" in out
    assert "all_agree=False" in out
    # reject 원인 가시화 — disagreement 케이스 증거(ref/cand 출력)가 요약에 노출
    assert "ans-i0" in out
    assert "wrong-i0" in out
