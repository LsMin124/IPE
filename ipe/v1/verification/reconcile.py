"""Reconciler (N5) — K golden + brute 상호 일치 확인 + canonical 채택 (Phase 3 M2).

병렬 Solution Synthesis 의 fan-in 코드 노드 로직. reference golden(최소 fanout_index)
을 기준으로 나머지 golden·brute 후보를 **differential**(M1 재사용)로 비교한다.

- 모든 후보가 reference 와 일치 → reference 를 canonical 채택 (all_agree=True).
- 하나라도 불일치(또는 crash/TLE) → reject 신호 (canonical None + disagreements).
- golden 부재 → 채택 불가. 후보 < 2 → 교차검증 불가 → 미채택 (vacuous 금지).

RFC §7.4 brute 독립성: 일치는 '독립 출처가 같은 답에 수렴' 이라야 의미가 있으므로
``origin`` 라벨로 출처를 기록만 하고, 신뢰 판단은 differential 결과로 한다.
LLM 없음 — runner 주입 (단위 테스트는 mock).
"""

from __future__ import annotations

from collections.abc import Sequence

from ..schema import ReconciliationResult, SolutionCandidate
from ._exec import (
    DEFAULT_MEMORY_LIMIT_MB,
    DEFAULT_TIME_LIMIT_MS,
    CodeRunner,
)
from .differential import DifferentialCase, run_differential

# disagreement 진단 상세 바운드 — reject 원인 가시화(케이스 증거)와 문자열 폭주 방지
# 사이 균형. 상세는 앞 케이스 일부면 충분(같은 원인 반복이 보통), 규모는 요약에 남는다.
_DETAIL_MAX_CASES = 3
_DETAIL_INPUT_HEAD = 60
_DETAIL_OUTPUT_HEAD = 40


def _head(text: str, limit: int) -> str:
    """한 줄 진단용 head — 공백/개행 접어 truncate (로그 친화)."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _describe_case(case: DifferentialCase) -> str:
    """불일치 케이스 한 건의 증거: 입력 + ref/cand 의 status·출력 head."""
    return (
        f"input={_head(case.input_text, _DETAIL_INPUT_HEAD)!r} "
        f"ref[{case.golden_status}]={_head(case.golden_output, _DETAIL_OUTPUT_HEAD)!r} "
        f"cand[{case.brute_status}]={_head(case.brute_output, _DETAIL_OUTPUT_HEAD)!r}"
    )


def reconcile(
    *,
    candidates: Sequence[SolutionCandidate],
    inputs: Sequence[str],
    runner: CodeRunner,
    time_limit_ms: int = DEFAULT_TIME_LIMIT_MS,
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
) -> ReconciliationResult:
    """후보들을 reference golden 기준 differential 로 reconcile."""
    count = len(candidates)
    goldens = sorted(
        (c for c in candidates if c.role == "golden"),
        key=lambda c: (c.fanout_index, c.origin),
    )
    if not goldens:
        return ReconciliationResult(
            candidate_count=count,
            all_agree=False,
            canonical_code=None,
            adopted_origin=None,
            disagreements=("no golden candidate to adopt as canonical",),
        )
    reference = goldens[0]
    others = [c for c in candidates if c is not reference]
    if not others:
        return ReconciliationResult(
            candidate_count=count,
            all_agree=False,
            canonical_code=None,
            adopted_origin=None,
            disagreements=("insufficient candidates for cross-check (need >= 2)",),
        )

    disagreements: list[str] = []
    for cand in sorted(others, key=lambda c: (c.role, c.fanout_index, c.origin)):
        report = run_differential(
            golden_code=reference.code,
            brute_code=cand.code,
            inputs=inputs,
            runner=runner,
            time_limit_ms=time_limit_ms,
            memory_limit_mb=memory_limit_mb,
        )
        if not report.all_agreed:
            detail = "; ".join(
                _describe_case(c)
                for c in report.disagreements[:_DETAIL_MAX_CASES]
            )
            disagreements.append(
                f"{reference.origin} vs {cand.origin} ({cand.role}): "
                f"differ on {len(report.disagreements)}/{report.total} input(s)"
                + (f" — {detail}" if detail else "")
            )

    all_agree = len(disagreements) == 0
    return ReconciliationResult(
        candidate_count=count,
        all_agree=all_agree,
        canonical_code=reference.code if all_agree else None,
        adopted_origin=reference.origin if all_agree else None,
        disagreements=tuple(disagreements),
    )
