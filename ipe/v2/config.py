"""v2 파이프라인 설정·상수 단일 소스 (M2 — 분산 상수 drift 차단).

모델명·iteration budget·recursion budget·토큰 단가가 ``main_v2``/``api``/``batch``/
``state`` 에 흩어져 있던 것을 한 모듈로 모은다. **값은 보존**한다 — CLI 와 API 가
의도적으로 다른 상한을 쓰는 정책(iteration 8 vs 4, recursion 동적 pad vs 고정 90)은
그대로 두되, 정의를 한 곳에서 선언해 **모델 교체·정책 변경 시 이 파일만 고치면 되게**
한다(두 곳 중 한 곳 빠뜨리는 drift 차단). 각 모듈은 자신이 노출하던 이름을 이 값의
alias 로 유지해 기존 import 경로를 보존한다.

무의존 leaf 모듈(다른 v2 모듈을 import 하지 않음) — 어디서든 안전하게 참조 가능.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ipe.v1.schema import QAReviewerKind

# --------------------------------------------------------------------------- #
# Phase 4 — P1/P2 생성 파이프라인 모드 (정확히 2개로 수렴)                        #
#   P1: 타겟 고정·단일(합성X)·공개(비은닉)·QA 3종(leakage 제외 — 타겟 공개라      #
#       유명문제 동형 게이트 부적합).                                            #
#   P2: 타겟 힌트·2+ 합성·은닉·QA 4종(leakage 포함).                             #
# --------------------------------------------------------------------------- #
PipelineMode = Literal["p1", "p2"]

QA_KINDS_P1: tuple[QAReviewerKind, ...] = ("ambiguity", "fairness", "difficulty")
QA_KINDS_P2: tuple[QAReviewerKind, ...] = (
    "ambiguity",
    "fairness",
    "leakage",
    "difficulty",
)


def mode_knobs(
    mode: PipelineMode,
) -> tuple[bool, Literal["single", "composed"], tuple[QAReviewerKind, ...]]:
    """모드 → (hidden, composition_mode, qa_kinds).

    P1=공개(hidden False)·단일(single)·QA 3종 / P2=은닉(True)·합성(composed)·4종.
    한 곳에서 선언해 main_v2/api/batch 가 동일 노브를 쓰게 한다 (drift 차단).
    """
    if mode == "p1":
        return (False, "single", QA_KINDS_P1)
    return (True, "composed", QA_KINDS_P2)


# --------------------------------------------------------------------------- #
# 모델명 (golden / brute) — 교체 시 여기 한 곳만                                  #
# --------------------------------------------------------------------------- #
GOLDEN_MODELS: tuple[str, ...] = ("claude-opus-4-8", "claude-sonnet-4-6")
BRUTE_MODEL = "claude-sonnet-4-6"  # golden 과 distinct → 독립 differential
GOLDEN_MODELS_CLI_DEFAULT = ",".join(GOLDEN_MODELS)  # argparse comma-sep default

# 난이도 calibration 모델 (RFC R4 — 사후 난이도 판별). QA Sonnet 승급과 동일 논리:
# calibration 은 정성 판단이라 약한 모델이면 분산↑. 패키지당 1콜이라 비용 경미.
DIFFICULTY_MODEL = "claude-sonnet-4-6"

# --------------------------------------------------------------------------- #
# iteration budget (narrative 재생성)                                           #
# --------------------------------------------------------------------------- #
MAX_ITERATIONS_DEFAULT = 8  # CLI / state 기본
MAX_ITERATIONS_API = 4  # API: 비용·지연 상한으로 더 낮게 (의도적 차이)

# --------------------------------------------------------------------------- #
# recursion budget                                                             #
#   CLI(main_v2): base + 활성 스테이지별 pad 누적(동적)                          #
#   API: 고정 상한                                                              #
# --------------------------------------------------------------------------- #
RECURSION_PAD_BASE = 15
RECURSION_PAD_SYNTHESIS = 12
RECURSION_PAD_SUITE = 6
RECURSION_PAD_QA = 14
RECURSION_LIMIT_API = 90

# --------------------------------------------------------------------------- #
# 토큰 단가 (USD per 1M tokens; input, output) — 비용 실측 정정(계약 §5, 5fb370f)  #
# --------------------------------------------------------------------------- #
PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "opus": (5.0, 25.0),
    "sonnet": (3.0, 15.0),
    "haiku": (1.0, 5.0),
}
