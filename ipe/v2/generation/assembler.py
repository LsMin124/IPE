"""suite assembler — verified golden 실행으로 pending TestSuite 의 expected 채움 (M4 step4).

step3 입력 생성기가 만든 pending TestSuite(expected=None)의 각 입력에 **검증된 golden**
(reconciled canonical, verification 통과)을 sandbox 실행해 stdout 을 expected 로 채운다
(RFC §7: 정답은 golden 실행으로 부트스트랩). 실행 도구는 Tier B 공용 ``run_code`` 재사용
(moat, 중복 0).

golden 이 실행 못하는 입력(crash/timeout)은 **drop** — 입력 직렬화↔골든 파서 불일치
또는 골든 버그의 신호다. assembled 케이스 수 / pending 수 = 규약 정합 비율(step5 anchor).
전부 실패면 ValueError (형식 불일치 가능 — known item).

**최소 생존율 게이트**: 생존율(filled/pending)이 ``_MIN_SURVIVAL_RATIO`` 미만이면
카테고리별 drop 집계를 담아 ValueError — 특정 tier 만 조용히 전멸한 약체 채점셋이
출하되는 것을 차단한다 (전멸(0개)만 잡던 기존 게이트의 강화, additive).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ipe.v1.schema import GeneratedTestCase, TestSuite
from ipe.v1.verification._exec import (
    DEFAULT_MEMORY_LIMIT_MB,
    DEFAULT_TIME_LIMIT_MS,
    run_code,
)

if TYPE_CHECKING:
    from ipe.sandbox.runner import RunResult
    from ipe.v1.verification._exec import CodeRunner

# 최소 생존율(filled/pending) — 미만이면 카테고리별 drop 집계와 함께 ValueError.
# 전멸(0개)만 잡던 게이트로는 특정 tier 전멸(예: large 만 전부 timeout)이 조용히
# 통과해 약체 채점셋이 출하됐다 — 그 침묵 경로를 차단하는 임계값.
_MIN_SURVIVAL_RATIO = 0.7


def assemble_suite(
    pending: TestSuite,
    golden_code: str,
    *,
    runner: CodeRunner,
    golden_origin: str,
    time_limit_ms: int = DEFAULT_TIME_LIMIT_MS,
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
) -> TestSuite:
    """golden 을 각 입력에 실행 → expected 채운 assembled TestSuite.

    실행 OK 인 케이스만 expected 채워 포함(나머지 drop). 전부 실패면 ValueError,
    생존율 < ``_MIN_SURVIVAL_RATIO`` 면 카테고리별 drop 집계를 담은 ValueError.
    ``golden_origin`` 은 provenance(어느 golden 이 정답을 만들었나).
    """
    filled: list[GeneratedTestCase] = []
    dropped_by_category: dict[str, int] = {}
    first_failure: RunResult | None = None
    first_failed_input: str | None = None
    for case in pending.cases:
        result = run_code(
            runner,
            golden_code,
            case.input_text,
            time_limit_ms=time_limit_ms,
            memory_limit_mb=memory_limit_mb,
        )
        if result.status == "OK":
            filled.append(
                case.model_copy(
                    update={
                        "expected_output": result.stdout.strip(),
                        # 계약 v1.0: 백엔드 TL 산정 근거 (max × 배수)
                        "golden_elapsed_ms": result.elapsed_ms,
                    }
                )
            )
        else:
            # (a) 카테고리별 drop 집계 — 어느 tier/edge 가 죽는지 진단·게이트 근거.
            dropped_by_category[case.category] = (
                dropped_by_category.get(case.category, 0) + 1
            )
            if first_failure is None:
                first_failure = result
                first_failed_input = case.input_text
    # 실패 증거(첫 실패 status/stderr/입력 head) — 두 게이트 메시지 공용 진단.
    detail = _first_failure_detail(first_failure, first_failed_input)
    if not filled:
        # 전부실패는 규약 불일치 신호 — 원인 분석 가능하게 첫 실패 증거 포함
        msg = (
            "assemble_suite: golden 이 생성 입력을 하나도 실행하지 못함 "
            f"(입력 직렬화↔골든 파서 불일치 가능){detail}"
        )
        raise ValueError(msg)
    survival = len(filled) / len(pending.cases)
    if survival < _MIN_SURVIVAL_RATIO:
        # (b) 최소 생존율 게이트 — 어떤 카테고리가 몇 개 죽었는지 담아 fail.
        drops = ", ".join(
            f"{cat}={n}" for cat, n in sorted(dropped_by_category.items())
        )
        msg = (
            f"assemble_suite: 생존율 {survival:.2f} < {_MIN_SURVIVAL_RATIO}"
            f" ({len(filled)}/{len(pending.cases)}) — 카테고리별 drop: {drops}"
            f" (조용한 약체 채점셋 출하 차단){detail}"
        )
        raise ValueError(msg)
    return TestSuite(cases=tuple(filled), golden_origin=golden_origin)


def _first_failure_detail(
    first_failure: RunResult | None, first_failed_input: str | None
) -> str:
    """첫 실패 증거(status/stderr/입력 head) 문자열 — 게이트 ValueError 진단 공용."""
    if first_failure is None or first_failed_input is None:
        return ""
    return (
        f" — 첫 실패: status={first_failure.status}"
        f" stderr={first_failure.stderr[:200]!r}"
        f" input_head={first_failed_input[:80]!r}"
    )
