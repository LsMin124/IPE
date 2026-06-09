"""real-LLM e2e — v2 full 파이프라인 (모델링 + synthesis + verification) (Phase 3 통합).

Marked ``@pytest.mark.e2e`` — CI 의 ``pytest -m "not e2e"`` 는 skip.
ANTHROPIC_API_KEY env 필요. 1 run ≈ strategist + formalizer + narrative +
faithfulness(모델링 4) + spec_bridge + designer + golden×2 + brute(synthesis 5) =
approx 9 LLM call + sandbox, cost approx $2-3 (재생성 시 +2/회).

Gate 의도(모델링 e2e 의 확장): **with_synthesis=True 그래프가 실 LLM 통합 경로에서
crash 없이 end 까지 도달** + valid final_status 종료 + 단계별 아티팩트 populate.
verification pass 여부는 **측정 대상(출하가능률 anchor)** 이지 gate 아님 — 1 run 은
파이프라인 배선 검증, 출하가능률(verification pass율)의 통계적 anchor 는 N>=3 follow-up.

Run::

    set -a; source .env; set +a
    .venv/bin/pytest -m e2e tests/v2/test_e2e_v2_full_pipeline_real_llm.py -v -s
"""

from __future__ import annotations

import os

import pytest

from ipe.v1.nodes import AnthropicCoderLLM
from ipe.v1.schema import TargetAlgorithm
from ipe.v2.graph import build_v2_graph
from ipe.v2.main_v2 import _normalize_final_state
from ipe.v2.state import initial_v2_state

# full 파이프라인의 valid terminal — success + 각 단계 거부.
VALID_FINAL_STATUSES = {
    "success",
    "fail_synthesis_rejected",  # golden/brute 불합의
    "fail_verification",  # 합의했으나 canonical 이 sample 불일치
    "fail_faithfulness",  # narrative round-trip 왜곡
    "fail_budget_exhausted",
}

_GOLDEN_MODELS = ["claude-opus-4-7", "claude-sonnet-4-6"]
_BRUTE_MODEL = "claude-sonnet-4-6"


@pytest.mark.e2e
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY missing — real LLM e2e skipped",
)
def test_v2_full_pipeline_single_run_real_llm() -> None:
    """1 run Dijkstra seed, hidden + synthesis — modeling+synthesis 실통합.

    검증(gate):
    - ``build_v2_graph(with_synthesis=True)`` + invoke 가 실 LLM 통합 path(모델링
      4-LLM + spec_bridge + designer + golden×2/brute + sandbox)에서 crash 없이 동작.
    - 모델링 아티팩트(strategy/blueprint/narrative/faithfulness) populate.
    - faithful 통과 시 synthesis 진입 — spec carry-over(target_algorithm=reduction_core),
      candidates fan-out(golden×2 + brute = 3, dedup reducer), reconciliation populate.
    - 합의 시 executor 검증까지 도달(attempt + verification populate).
    - ``final_status`` 가 valid enum 으로 종료.

    측정(anchor, gate 아님): final_status + verification.overall_pass 를 출력 —
    출하가능률(verification pass) 1 data point.
    """
    graph = build_v2_graph(
        hidden=True,
        with_synthesis=True,
        golden_llms=[AnthropicCoderLLM(m) for m in _GOLDEN_MODELS],
        brute_llm=AnthropicCoderLLM(_BRUTE_MODEL),
        golden_origins=_GOLDEN_MODELS,
    )
    raw = graph.invoke(
        initial_v2_state(
            "e2e-v2-full-dijkstra", TargetAlgorithm.DIJKSTRA, max_iterations=4
        ),
        config={"recursion_limit": 60},
    )
    final = _normalize_final_state(raw)

    # ---- gate: 파이프라인 배선 ----
    assert final.final_status in VALID_FINAL_STATUSES, final.final_status
    assert final.strategy is not None
    assert final.blueprint is not None
    assert final.narrative is not None
    assert final.narrative.hidden is True
    assert final.faithfulness is not None  # round-trip 도달

    if final.faithfulness.faithful:
        # faithful → synthesis 진입: spec 파생 + carry-over
        assert final.spec is not None
        assert final.spec.target_algorithm is final.blueprint.reduction_core
        # golden×2 + brute fan-out (origin 라벨 distinct → dedup 후 3)
        assert len(final.candidates) == 3, [c.origin for c in final.candidates]
        assert final.reconciliation is not None
        if final.reconciliation.all_agree:
            # 합의 → synth_bridge → executor 검증까지
            assert final.attempt is not None
            assert final.verification is not None

    # ---- 측정: 출하가능률 anchor (1 data point) ----
    verification_pass = (
        final.verification.overall_pass if final.verification is not None else None
    )
    reconciled = (
        final.reconciliation.all_agree
        if final.reconciliation is not None
        else None
    )
    print(
        f"\n[e2e-anchor] final_status={final.final_status} "
        f"faithful={final.faithfulness.faithful} "
        f"reconciled={reconciled} verification_pass={verification_pass} "
        f"candidates={len(final.candidates)} iteration={final.iteration}"
    )
