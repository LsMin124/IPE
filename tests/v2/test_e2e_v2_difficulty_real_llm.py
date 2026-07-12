"""난이도 calibration 실 LLM e2e (RFC R4) — 수동/유료, 기본 게이트 제외.

실행::

    set -a; source .env; set +a
    .venv/bin/pytest -m e2e tests/v2/test_e2e_v2_difficulty_real_llm.py -v -s

실제 anchor(``ipe.calibration.anchors.json``) 대비 BOJ 티어를 산출하는지 검산한다 —
유효 티어 enum + 비어있지 않은 reasoning + 인용 anchor 가 로드 집합의 부분집합.
"""

from __future__ import annotations

import os

import pytest

from ipe.v1.schema.difficulty import _TIERS

_DIJKSTRA_PACKAGE = {
    "problem": {
        "title": "상수도 배관망 최소 비용",
        "description": (
            "V개 시설과 E개 양방향 배관(양수 비용)이 있다. 1번 시설에서 각 시설까지의 "
            "최소 누적 비용을 구한다. 도달 불가는 -1."
        ),
        "io_contract": {
            "input_format": "V E\\n다음 E줄: u v w",
            "output_format": "1번에서 각 시설까지 최소비용 (공백 구분)",
        },
        "constraints": [
            {"name": "V", "min_value": 1, "max_value": 20000},
            {"name": "E", "min_value": 0, "max_value": 300000},
        ],
        "sample_testcases": [
            {"input_text": "3 2\\n1 2 5\\n2 3 7", "expected_output": "0 5 12"},
        ],
    },
    "solution": {
        "golden_code": (
            "import heapq, sys\\n"
            "def main():\\n"
            "    data = sys.stdin.read().split()\\n"
            "    # 다익스트라 (priority queue)\\n"
            "    print('0')\\n"
        ),
        "language": "python",
    },
    "test_suite": {"cases": [], "origin": "opus"},
    "meta": {"hidden_algorithm": "dijkstra", "composition": []},
}


@pytest.mark.e2e
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY missing — real LLM e2e skipped",
)
def test_real_llm_calibrates_dijkstra_to_valid_tier() -> None:
    from ipe.v2.difficulty import AnthropicDifficultyLLM, evaluate_difficulty

    report = evaluate_difficulty(_DIJKSTRA_PACKAGE, llm=AnthropicDifficultyLLM())

    assert report.tier in _TIERS  # 유효 BOJ 티어
    assert report.label.split()[0] == report.tier  # label↔tier 일관
    assert report.reasoning.strip()  # 근거 비어있지 않음
    assert report.factors.algorithm  # 지배 알고리즘 명시
    # 인용 anchor 는 로드된 집합의 부분집합 (환각 id 필터됨)
    from ipe.calibration import load_anchors

    known = {a.get("id") for a in load_anchors()}
    assert set(report.calibration_anchors) <= known
    print(f"\n[e2e] dijkstra → {report.label} ({report.reasoning})")
