"""real-LLM e2e — v2 P1/P2 풀 파이프라인 (Phase 4 — QA 게이트 포함).

Marked ``@pytest.mark.e2e`` — CI 의 ``pytest -m "not e2e"`` 는 skip.
ANTHROPIC_API_KEY env 필요. 1 run ≈ suite 파이프라인(~10 call) + QA Haiku×(3|4)
= approx $0.4~0.6 (QA 는 저가 tier 라 증분 미미).

Gate 의도: **P1/P2 모드 그래프가 실 LLM 통합 경로에서 crash 없이 valid 종료** +
QA 스테이지 도달 시 모드별 qa_kinds 만큼 리뷰/qa_report populate. ``fail_qa`` 도 valid
종료(게이트가 일하는 증거). 모드 검산: P1=composition 빈값·QA 3종(leakage 제외) /
P2=composition≥1·QA 4종.

측정(anchor, gate 아님): **모드별 QA 게이트 통과율** + composition 실현 — 1 data point.

Run::

    set -a; source .env; set +a
    .venv/bin/pytest -m e2e tests/v2/test_e2e_v2_qa_real_llm.py -v -s
"""

from __future__ import annotations

import os

import pytest

from ipe.v1.nodes import AnthropicCoderLLM
from ipe.v1.schema import TargetAlgorithm
from ipe.v2.config import mode_knobs
from ipe.v2.graph import build_v2_graph
from ipe.v2.main_v2 import _normalize_final_state
from ipe.v2.state import initial_v2_state

VALID_FINAL_STATUSES = {
    "success",
    "fail_spec_authoring",  # spec_bridge structured output 전멸 (가드 종료)
    "fail_synthesis_rejected",
    "fail_verification",
    "fail_faithfulness",
    "fail_qa",
    "fail_budget_exhausted",
}

_GOLDEN_MODELS = ["claude-opus-4-8", "claude-sonnet-4-6"]
_BRUTE_MODEL = "claude-sonnet-4-6"


@pytest.mark.e2e
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY missing — real LLM e2e skipped",
)
@pytest.mark.parametrize("mode", ["p1", "p2"])
def test_v2_pipeline_single_run_real_llm(mode: str) -> None:
    """1 run DIJKSTRA seed — 모드별 모델링+synthesis+suite+QA 실통합.

    검증(gate):
    - 모드 그래프 invoke 가 crash 없이 valid final_status 로 종료.
    - QA 스테이지 도달(suite assembled) 시: qa_reviews 가 모드 qa_kinds 와 일치
      (P1 3종 / P2 4종), qa_report populate, final_status 는 success 또는 fail_qa.
    - success 면 qa_report.overall_pass True.
    - 모드 검산: P1 composition 빈값(단일) / P2 composition≥1(합성, 총 2개+).

    측정(anchor): kind 별 passed + final_status + composition — 모드별 1 data point.
    """
    hidden, composition_mode, qa_kinds = mode_knobs(mode)  # type: ignore[arg-type]
    graph = build_v2_graph(
        hidden=hidden,
        composition_mode=composition_mode,
        golden_llms=[
            AnthropicCoderLLM(m, parse_discipline=True) for m in _GOLDEN_MODELS
        ],
        brute_llm=AnthropicCoderLLM(_BRUTE_MODEL, parse_discipline=True),
        golden_origins=_GOLDEN_MODELS,
        with_test_suite=True,
        with_qa=True,
        qa_kinds=qa_kinds,
    )
    raw = graph.invoke(
        initial_v2_state(
            f"e2e-v2-{mode}-dijkstra", TargetAlgorithm.DIJKSTRA, max_iterations=4
        ),
        config={"recursion_limit": 90},  # back-route(B) revise 사이클 여유
    )
    final = _normalize_final_state(raw)

    # ---- gate ----
    assert final.final_status in VALID_FINAL_STATUSES, final.final_status
    assert final.faithfulness is not None

    suite_reached = final.test_suite is not None and final.test_suite.is_assembled
    if suite_reached:
        # QA 스테이지 도달 — 모드 qa_kinds 만큼 리뷰 (back-route 시 라운드 누적)
        assert {r.kind for r in final.qa_reviews} == set(qa_kinds)
        assert final.qa_report is not None
        assert len(final.qa_report.reviews) == len(qa_kinds)  # kind 별 최신만
        assert final.final_status in ("success", "fail_qa")
        if final.final_status == "success":
            assert final.qa_report.overall_pass is True
        # 모드별 composition 검산 (suite 도달 시 blueprint 존재)
        composition = (
            [a.value for a in final.blueprint.composition]
            if final.blueprint is not None
            else []
        )
        if mode == "p1":
            assert composition == [], composition  # 단일 — 합성 없음
        else:
            assert len(composition) >= 1, composition  # 합성 — 총 2개 이상
    else:
        # 상류(검증 등)에서 멈춤 — QA 미진입
        assert final.qa_report is None

    # ---- 측정: 모드별 QA 게이트 anchor (1 data point) ----
    verdicts = (
        {r.kind: r.passed for r in final.qa_report.reviews}
        if final.qa_report is not None
        else None
    )
    findings = (
        sum(len(r.findings) for r in final.qa_reviews) if final.qa_reviews else 0
    )
    suite_size = len(final.test_suite.cases) if final.test_suite is not None else None
    composition = (
        [a.value for a in final.blueprint.composition]
        if final.blueprint is not None
        else None
    )
    print(
        f"\n[e2e-{mode}-anchor] final_status={final.final_status} "
        f"composition={composition} "  # 모드 실현: P1=[] / P2≥1
        f"suite_cases={suite_size} qa_verdicts={verdicts} "
        f"findings_total={findings} iteration={final.iteration} "
        f"qa_routebacks={final.qa_routebacks}"
    )
    if final.qa_report is not None and not final.qa_report.overall_pass:
        for r in final.qa_report.reviews:
            if not r.passed:
                worst = [f"{f.severity}:{f.description[:80]}" for f in r.findings]
                print(
                    f"[e2e-{mode}-diag] kind={r.kind} rationale={r.rationale[:120]!r} "
                    f"findings={worst}"
                )

    # ---- 진단: synthesis reject 원인 (disagreement 케이스 증거) ----
    rec = final.reconciliation
    if rec is not None and not rec.all_agree:
        for d in rec.disagreements:
            print(f"[e2e-{mode}-diag] reconcile: {d}")

    # ---- 진단: spec 저작 실패 가드 발동 (BS-run3 crash 클래스 — 사유 가시화) ----
    if final.spec_authoring_error is not None:
        print(f"[e2e-{mode}-diag] spec_authoring_error: {final.spec_authoring_error}")
