"""real-LLM e2e — v2 풀 채점셋 파이프라인 (모델링+synthesis+verification+test-suite) (M4 step5).

Marked ``@pytest.mark.e2e`` — CI 의 ``pytest -m "not e2e"`` 는 skip.
ANTHROPIC_API_KEY env 필요. 1 run ≈ 모델링 4 + spec_bridge + designer + golden×2 +
brute + generator_designer = approx 10 LLM call + sandbox, cost approx $2.5-3.5.

Gate 의도(full e2e 의 확장): **with_test_suite=True 그래프가 실 LLM 통합 경로에서
crash 없이 end 까지 도달** + 검증 통과 시 채점셋이 assembled 로 populate. seed 는
DIJKSTRA — verification 통과 이력 1/1 인 seed (suite 단계 도달 확률 최고) + step3b
graph io(weighted_edges 직렬화·formalizer 단일 graph 필드 규율)의 실측 경로.
(이전 SORT 2 run 은 둘 다 fail_verification — 실패경로 배선만 검증, anchor 미확보.)

측정(anchor, gate 아님): **assembled/planned 비율 = 입력 직렬화 규약↔골든 파서 정합**
(known item — 생성 입력의 canonical 직렬화와 golden 의 파서가 독립 산출이라 실측 필요).
- ratio 1.0 = 규약 완전 정합, 0<ratio<1 = 부분 drop, 전부실패 = assembler ValueError
  crash(테스트 실패로 표면화 — 그 자체가 known-item 측정 결과).
- verification fail 시 sample 진단(expected vs actual/stderr) 출력 — 출하가능률
  변동(sort 0/2)의 원인 데이터.

Run::

    set -a; source .env; set +a
    .venv/bin/pytest -m e2e tests/v2/test_e2e_v2_suite_real_llm.py -v -s
"""

from __future__ import annotations

import os

import pytest

from ipe.v1.nodes import AnthropicCoderLLM
from ipe.v1.schema import TargetAlgorithm
from ipe.v2.graph import build_v2_graph
from ipe.v2.main_v2 import _normalize_final_state
from ipe.v2.state import initial_v2_state

from .test_e2e_v2_full_pipeline_real_llm import VALID_FINAL_STATUSES

_GOLDEN_MODELS = ["claude-opus-4-8", "claude-sonnet-4-6"]
_BRUTE_MODEL = "claude-sonnet-4-6"


@pytest.mark.e2e
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY missing — real LLM e2e skipped",
)
def test_v2_suite_pipeline_single_run_real_llm() -> None:
    """1 run DIJKSTRA seed, hidden + synthesis + test-suite — M4 풀 채점셋 실통합.

    검증(gate):
    - ``build_v2_graph(with_test_suite=True)`` + invoke 가 실 LLM 통합 path 에서
      crash 없이 valid final_status 로 종료.
    - ``final_status == "success"`` 면 (with_test_suite 에선 suite_assembler 를
      거쳐야만 end_success) generator_contract + assembled test_suite populate,
      모든 케이스 expected 채워짐, provenance = reconciliation.adopted_origin.
    - 검증 실패 경로면 suite 미진입 (test_suite None).

    측정(anchor): planned(contract.total_planned_cases) vs assembled(len cases)
    비율 출력 — 직렬화 규약 정합 1 data point.
    """
    graph = build_v2_graph(
        hidden=True,
        golden_llms=[
            AnthropicCoderLLM(m, parse_discipline=True) for m in _GOLDEN_MODELS
        ],
        brute_llm=AnthropicCoderLLM(_BRUTE_MODEL, parse_discipline=True),
        golden_origins=_GOLDEN_MODELS,
        with_test_suite=True,
    )
    raw = graph.invoke(
        initial_v2_state(
            "e2e-v2-suite-dijkstra", TargetAlgorithm.DIJKSTRA, max_iterations=4
        ),
        config={"recursion_limit": 60},
    )
    final = _normalize_final_state(raw)

    # ---- gate: 파이프라인 배선 ----
    assert final.final_status in VALID_FINAL_STATUSES, final.final_status
    assert final.strategy is not None
    assert final.blueprint is not None
    assert final.narrative is not None
    assert final.faithfulness is not None

    if final.final_status == "success":
        # success = 검증 통과 + 채점셋 assembled (suite_assembler 경유만 end_success)
        assert final.verification is not None
        assert final.verification.overall_pass is True
        contract = final.generator_contract
        suite = final.test_suite
        assert contract is not None
        assert suite is not None
        assert suite.is_assembled is True
        assert all(c.expected_output is not None for c in suite.cases)
        assert all(c.input_text for c in suite.cases)
        if final.reconciliation is not None:
            assert suite.golden_origin == final.reconciliation.adopted_origin
    else:
        # 실패 경로(왜곡/불합의/검증실패)에선 suite 미진입
        assert final.test_suite is None

    # ---- 측정: assembled/planned 비율 anchor (1 data point) ----
    planned = (
        final.generator_contract.total_planned_cases
        if final.generator_contract is not None
        else None
    )
    assembled = len(final.test_suite.cases) if final.test_suite is not None else None
    ratio = (
        f"{assembled / planned:.3f}"
        if planned is not None and assembled is not None
        else "n/a"
    )
    verification_pass = (
        final.verification.overall_pass if final.verification is not None else None
    )
    print(
        f"\n[e2e-suite-anchor] final_status={final.final_status} "
        f"faithful={final.faithfulness.faithful} "
        f"verification_pass={verification_pass} "
        f"planned={planned} assembled={assembled} ratio={ratio} "
        f"iteration={final.iteration}"
    )

    # ---- 진단: verification fail 의 원인 데이터 (출하가능률 변동 분석용) ----
    v = final.verification
    if v is not None and not v.overall_pass:
        print(
            f"[e2e-suite-diag] failure_mode={v.failure_mode} "
            f"samples_engaged={v.samples_engaged} "
            f"invariant_violations={[iv.invariant_kind for iv in v.invariant_violations]}"
        )
        for s in v.sample_results:
            if not s.passed:
                print(
                    f"[e2e-suite-diag] sample#{s.index} "
                    f"expected={s.expected_output[:80]!r} "
                    f"actual={s.actual_output[:80]!r} stderr={s.stderr[:120]!r}"
                )

    # ---- 진단: synthesis reject 원인 (disagreement 케이스 증거) ----
    rec = final.reconciliation
    if rec is not None and not rec.all_agree:
        for d in rec.disagreements:
            print(f"[e2e-suite-diag] reconcile: {d}")
