"""suite assembler 엔진 단위 테스트 (Phase 3 M4 step4).

assemble_suite(pending, golden_code, runner, origin): golden 실행으로 expected 채움 +
실행 실패 케이스 drop + 전부 실패 시 ValueError + 생존율 < _MIN_SURVIVAL_RATIO 면
카테고리별 drop 집계 담은 ValueError(약체 채점셋 출하 차단). mock runner 로 sandbox
없이 결정론.
"""

from __future__ import annotations

import pytest

from ipe.sandbox.runner import RunResult, RunSpec
from ipe.v1.schema import GeneratedTestCase, TestSuite
from ipe.v2.generation.assembler import assemble_suite


class _EchoRunner:
    """golden 출력 = f'ans:{stdin}'. 'BAD' 포함 입력은 RTE(실행 실패)."""

    def run(self, spec: RunSpec) -> RunResult:
        if "BAD" in spec.stdin:
            return RunResult(
                status="RTE", returncode=1, stdout="", stderr="boom", elapsed_ms=1
            )
        return RunResult(
            status="OK",
            returncode=0,
            stdout=f"ans:{spec.stdin}\n",
            stderr="",
            elapsed_ms=1,
        )


def _pending(*inputs: str) -> TestSuite:
    return TestSuite(
        cases=tuple(
            GeneratedTestCase(input_text=i, category="small") for i in inputs
        )
    )


def test_assemble_fills_expected_from_golden_stdout() -> None:
    suite = assemble_suite(
        _pending("1", "2", "3"), "# golden", runner=_EchoRunner(), golden_origin="opus"
    )
    assert suite.is_assembled is True
    assert suite.golden_origin == "opus"
    assert [c.expected_output for c in suite.cases] == ["ans:1", "ans:2", "ans:3"]


def test_assemble_drops_cases_golden_cannot_run() -> None:
    # 4개 중 1개 실패(생존율 0.75 ≥ 0.7) — drop 만 하고 통과하는 경로.
    suite = assemble_suite(
        _pending("1", "BAD", "3", "4"), "# g", runner=_EchoRunner(), golden_origin="g"
    )
    assert len(suite.cases) == 3  # BAD drop
    assert all(c.expected_output is not None for c in suite.cases)
    assert [c.input_text for c in suite.cases] == ["1", "3", "4"]


def test_assemble_raises_when_all_cases_fail() -> None:
    with pytest.raises(ValueError, match="하나도 실행") as exc_info:
        assemble_suite(
            _pending("BAD1", "BAD2"), "# g", runner=_EchoRunner(), golden_origin="g"
        )
    msg = str(exc_info.value)
    assert "RTE" in msg  # 첫 실패 status 진단 (e2e all-fail 원인 분석용)
    assert "boom" in msg  # 첫 실패 stderr
    assert "BAD1" in msg  # 첫 실패 입력 head


def _pending_by_category(**inputs_by_category: tuple[str, ...]) -> TestSuite:
    """카테고리별 입력으로 pending suite 구성 — 생존율 게이트의 drop 집계 검증용."""
    return TestSuite(
        cases=tuple(
            GeneratedTestCase(input_text=i, category=cat)
            for cat, inputs in inputs_by_category.items()
            for i in inputs
        )
    )


def test_assemble_raises_below_min_survival_with_category_drops() -> None:
    """생존율(4/10=0.4) < 0.7 → 어떤 카테고리가 몇 개 죽었는지 담은 ValueError
    (특정 tier 만 조용히 전멸한 약체 채점셋 출하 차단)."""
    pending = _pending_by_category(
        small=("1", "2", "3", "4"),
        large=("BAD-l1", "BAD-l2", "BAD-l3"),
        stress=("BAD-s1", "BAD-s2", "BAD-s3"),
    )
    with pytest.raises(ValueError, match="생존율") as exc_info:
        assemble_suite(pending, "# g", runner=_EchoRunner(), golden_origin="g")
    msg = str(exc_info.value)
    assert "4/10" in msg  # filled/pending
    assert "large=3" in msg  # 카테고리별 drop 집계
    assert "stress=3" in msg
    assert "small=" not in msg  # 생존 tier 는 drop 집계에 미포함
    assert "RTE" in msg  # 첫 실패 증거(원인 분석용) 동봉


def test_assemble_allows_drops_at_or_above_min_survival() -> None:
    """생존율 7/10=0.7(임계 이상) — drop 이 있어도 통과 (경계 포함 확인)."""
    pending = _pending_by_category(
        small=("1", "2", "3", "4", "5", "6", "7"),
        large=("BAD1", "BAD2", "BAD3"),
    )
    suite = assemble_suite(pending, "# g", runner=_EchoRunner(), golden_origin="g")
    assert len(suite.cases) == 7
    assert suite.is_assembled is True
