"""suite_assembler 노드 — pending TestSuite + verified golden → assembled (M4 step4).

assembler 엔진(``generation.assembler``)을 감싸는 노드. **LLM 없음**. state.test_suite
(step3 pending) + state.attempt(reconciled canonical, verification 통과한 golden)을 받아
각 입력에 golden 실행 → expected 채운 assembled TestSuite 로 교체. golden_origin 은
reconciliation.adopted_origin(provenance).

**resolved_edges 편입** (Phase 5a 후속): edge_filler 가 canonical golden 실행으로
expected 를 이미 채운 ``state.resolved_edges``(min/unreachable 등 검증된 퇴화 입력)를
``category="edge_resolved:<name>"`` 케이스로 채점셋에 병합한다 — meta 로만 나가고
채점셋에 빠지던 정답-검증 퇴화 케이스의 편입. 동일 ``input_text`` 가 이미 있으면
skip(중복 없음), expected 미채움(pending) 엣지는 편입하지 않는다(진단 보존).

runner None 이면 production sandbox(pick_runner) — executor 와 동일.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ipe.v1.schema import GeneratedTestCase, TestSuite

from ..generation.assembler import assemble_suite
from ..state import V2State

if TYPE_CHECKING:
    from ipe.v1.schema import ResolvedEdgeCase
    from ipe.v1.verification._exec import CodeRunner


def make_suite_assembler_node(
    *,
    runner: CodeRunner | None = None,
) -> Callable[[V2State], V2State]:
    """factory — pending TestSuite + verified golden(attempt) → assembled TestSuite.

    test 는 mock runner 주입. None 이면 production sandbox.
    """
    resolved_runner: CodeRunner = runner if runner is not None else _default_runner()

    def node(state: V2State) -> V2State:
        suite = state.test_suite
        attempt = state.attempt
        if suite is None or attempt is None:
            msg = "suite_assembler requires state.test_suite and state.attempt"
            raise ValueError(msg)
        origin = "golden"
        if state.reconciliation is not None and state.reconciliation.adopted_origin:
            origin = state.reconciliation.adopted_origin
        assembled = assemble_suite(
            suite, attempt.code, runner=resolved_runner, golden_origin=origin
        )
        merged = _merge_resolved_edges(assembled, state.resolved_edges)
        return state.model_copy(update={"test_suite": merged})

    return node


def _merge_resolved_edges(
    suite: TestSuite, resolved_edges: tuple[ResolvedEdgeCase, ...]
) -> TestSuite:
    """golden-검증된 resolved_edges 를 채점셋에 중복 없이 병합 (순수 함수).

    expected_output 이 채워진(=edge_filler 가 canonical golden 으로 정의한) 엣지만
    ``category="edge_resolved:<name>"`` 케이스로 덧붙인다. 동일 ``input_text`` 가 suite
    에(또는 앞선 엣지에) 이미 있으면 skip — 같은 퇴화 입력이 contract edge_cases 로도
    생성될 수 있어 이중 채점을 막는다. 편입분 golden_elapsed_ms 는 None(퇴화 입력은
    소규모라 TL 산정 max 에 기여하지 않음 — api 집계는 None 필터).
    """
    seen = {c.input_text for c in suite.cases}
    extra: list[GeneratedTestCase] = []
    for edge in resolved_edges:
        if edge.expected_output is None or edge.input_text in seen:
            continue  # pending(미정의) 엣지/중복 입력은 편입하지 않음
        seen.add(edge.input_text)
        extra.append(
            GeneratedTestCase(
                input_text=edge.input_text,
                category=f"edge_resolved:{edge.name}",
                expected_output=edge.expected_output,
            )
        )
    if not extra:
        return suite
    return suite.model_copy(update={"cases": suite.cases + tuple(extra)})


def _default_runner() -> CodeRunner:
    from ipe.sandbox.selector import pick_runner

    return pick_runner()
