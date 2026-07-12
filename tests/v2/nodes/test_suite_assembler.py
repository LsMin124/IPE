"""suite_assembler 노드 단위 테스트 (Phase 3 M4 step4).

- ``make_suite_assembler_node`` (LLM 없음): pending test_suite + verified golden(attempt)
  → assembled(expected 채움). golden_origin=reconciliation.adopted_origin.
- resolved_edges 편입: edge_filler 가 golden 으로 expected 채운 퇴화 엣지를
  ``edge_resolved:<name>`` 케이스로 중복 없이 병합 (pending 엣지/동일 input_text skip).
"""

from __future__ import annotations

import pytest

from ipe.sandbox.runner import RunResult, RunSpec
from ipe.v1.schema import (
    GeneratedTestCase,
    ReconciliationResult,
    ResolvedEdgeCase,
    SolutionAttempt,
    TargetAlgorithm,
    TestSuite,
)
from ipe.v2.nodes import make_suite_assembler_node
from ipe.v2.state import V2State, initial_v2_state


class _EchoRunner:
    def run(self, spec: RunSpec) -> RunResult:
        return RunResult(
            status="OK",
            returncode=0,
            stdout=f"out:{spec.stdin}\n",
            stderr="",
            elapsed_ms=1,
        )


def _pending() -> TestSuite:
    return TestSuite(
        cases=(
            GeneratedTestCase(input_text="1", category="small"),
            GeneratedTestCase(input_text="2", category="small"),
        )
    )


def _state(
    *,
    with_suite: bool = True,
    with_attempt: bool = True,
    origin: str = "opus",
    resolved_edges: tuple[ResolvedEdgeCase, ...] = (),
) -> V2State:
    base = initial_v2_state("run-v2", TargetAlgorithm.SORT)
    update: dict[str, object] = {"resolved_edges": resolved_edges}
    if with_suite:
        update["test_suite"] = _pending()
    if with_attempt:
        update["attempt"] = SolutionAttempt(code="# golden", iteration=0)
        update["reconciliation"] = ReconciliationResult(
            candidate_count=3,
            all_agree=True,
            canonical_code="# golden",
            adopted_origin=origin,
        )
    return base.model_copy(update=update)


def test_assembler_node_fills_and_sets_origin() -> None:
    out = make_suite_assembler_node(runner=_EchoRunner())(_state())
    suite = out.test_suite
    assert suite is not None
    assert suite.is_assembled is True
    assert suite.golden_origin == "opus"
    assert [c.expected_output for c in suite.cases] == ["out:1", "out:2"]


def test_assembler_records_golden_elapsed_ms() -> None:
    """B2C 계약 v1.0: 케이스별 golden 실행시간을 기록 — 백엔드가 문제별
    TL(시간제한)을 max_golden_elapsed_ms × 배수로 산정하는 근거."""
    out = make_suite_assembler_node(runner=_EchoRunner())(_state())
    suite = out.test_suite
    assert suite is not None
    assert [c.golden_elapsed_ms for c in suite.cases] == [1, 1]


def test_assembler_node_requires_suite_and_attempt() -> None:
    node = make_suite_assembler_node(runner=_EchoRunner())
    with pytest.raises(ValueError, match="test_suite"):
        node(_state(with_suite=False))
    with pytest.raises(ValueError, match="attempt"):
        node(_state(with_attempt=False))


def test_assembler_node_preserves_pending_original() -> None:
    state = _state()
    out = make_suite_assembler_node(runner=_EchoRunner())(state)
    assert state.test_suite is not None and state.test_suite.is_assembled is False
    assert out.test_suite is not None and out.test_suite.is_assembled is True


def test_assembler_merges_verified_resolved_edges() -> None:
    """edge_filler 가 golden 으로 expected 채운 resolved_edges 를 채점셋에
    ``edge_resolved:<name>`` 케이스로 편입 — golden 검증 expected 를 재실행 없이
    보존(runner 출력 'out:...' 이 아니라 edge_filler 채움값 그대로)."""
    edges = (
        ResolvedEdgeCase(name="min", input_text="9", expected_output="0"),
        ResolvedEdgeCase(name="unreachable", input_text="8", expected_output="-1"),
    )
    out = make_suite_assembler_node(runner=_EchoRunner())(
        _state(resolved_edges=edges)
    )
    suite = out.test_suite
    assert suite is not None
    assert len(suite.cases) == 4  # pending 2 + 편입 2
    merged = {c.category: c for c in suite.cases[2:]}
    assert merged["edge_resolved:min"].input_text == "9"
    assert merged["edge_resolved:min"].expected_output == "0"  # 재실행 아님
    assert merged["edge_resolved:unreachable"].expected_output == "-1"
    assert merged["edge_resolved:min"].golden_elapsed_ms is None  # TL 산정 무기여
    assert suite.is_assembled is True


def test_assembler_skips_pending_and_duplicate_resolved_edges() -> None:
    """expected 미채움(pending) 엣지·suite 에 이미 있는 input_text 는 편입 skip
    (중복 채점·미정의 정답 유입 차단)."""
    edges = (
        # "1" 은 pending suite 의 케이스와 동일 input_text → skip
        ResolvedEdgeCase(name="min", input_text="1", expected_output="0"),
        # expected=None (golden 실행 실패 pending) → skip
        ResolvedEdgeCase(name="unreachable", input_text="7", expected_output=None),
        # 앞선 편입 엣지와 동일 input_text → skip (엣지 간 중복도 차단)
        ResolvedEdgeCase(name="min2", input_text="9", expected_output="0"),
        ResolvedEdgeCase(name="min3", input_text="9", expected_output="0"),
    )
    out = make_suite_assembler_node(runner=_EchoRunner())(
        _state(resolved_edges=edges)
    )
    suite = out.test_suite
    assert suite is not None
    assert len(suite.cases) == 3  # pending 2 + 편입은 min2 하나만
    assert suite.cases[2].category == "edge_resolved:min2"


def test_assembler_without_resolved_edges_is_unchanged() -> None:
    """resolved_edges 빈(비-graph 등) 경로 — 편입 없이 기존 assembled 그대로."""
    out = make_suite_assembler_node(runner=_EchoRunner())(_state())
    suite = out.test_suite
    assert suite is not None
    assert [c.category for c in suite.cases] == ["small", "small"]
