"""qa_reviewer 노드 — 문제 패키지 5관점 병렬 QA (M5 step2, RFC N10a-e).

suite 까지 완성된 패키지(narrative+spec+test_suite)를 모호성/공정성/유출/난이도/
지문품질(presentation) 5 관점의 리뷰어가 **병렬 fan-out** 으로 검토한다. 공용
factory 1개 + kind 별 관점 헌장(charter) — 모델은 전부 Haiku (RFC 비용 관찰:
QA=저가 tier).

- 병렬 규율(M0/M2): 노드는 **partial dict** ``{"qa_reviews": [review]}`` 반환 —
  ``qa_reviews`` reducer 채널에 누적 (dedup 멱등).
- freeze 규율: ``review.kind`` 는 node 의 kind 로 강제 스탬프 (LLM 못 바꿈).
- 유출 리뷰어는 LLM 의 유명 문제 동형성 지식으로 판단 — reference corpus 조회는
  별도 과제 이연 (RFC Q2). 난이도 리뷰어는 명백한 모순 sanity 만 (calibration 은
  별도 RFC, R4 — 난이도-agnostic 원칙 유지).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any, Protocol

from ipe.v1.schema import QAReview, QAReviewerKind, is_basic

from ..generation.input_gen import format_constraint
from ..state import V2State

# QA 는 출하 여부를 정하는 **최종 품질 게이트** — ambiguity/fairness/leakage/difficulty
# 는 가장 미묘한 정성 판단이라 상류(Sonnet/Opus)보다 약한 모델이면 오탈락(좋은 문제
# fail_qa)·오통과(나쁜 문제 published) 양방향 리스크. Haiku→Sonnet 승급: 판정 보정 +
# routeback revise 피드백 질 ↑ (in-run 회수율 개선 기대). 비용은 charter×4 만큼 증가.
QA_REVIEWER_MODEL = "claude-sonnet-4-6"
QA_REVIEWER_TEMPERATURE = 0.0  # 판정 일관성 (발산 금지)

_CHARTERS: dict[QAReviewerKind, str] = {
    "ambiguity": (
        "모호성 — 지문과 입출력 형식이 **유일하게 해석**되는가. 미정의 동작, "
        "모호한 경계 조건(빈 입력/동률/중복), 불명확한 출력 형식(공백·순서·반올림)을 "
        "찾는다. 체크리스트: (1) 동률(tie)·중복 원소·퇴화 입력의 출력 의미가 지문에 "
        "**결정**돼 있는가, (2) 출력 형식의 해석이 유일한가(구분자·순서·정밀도), "
        "(3) 제약(constraints)과 지문 본문의 수치가 서로 모순되지 않는가."
    ),
    "fairness": (
        "공정성 — solver 가 지문과 형식 계약만으로 풀 수 있는가. 지문에 없는 숨은 "
        "전제, 도메인 사전지식 요구, 불공정한 함정을 찾는다. 단 **알고리즘 은닉 "
        "자체는 의도된 설계** — 결함으로 지적하지 말 것. 추가 점검: 지문의 시나리오가 "
        "**도메인 전문지식·문화 특수 지식**(특정 업계 관행, 지역·문화권 한정 상식 등)을 "
        "지문 설명 없이 전제하면 결함이다."
    ),
    "leakage": (
        "유출 — 이 문제가 유명 문제(온라인 저지/교재의 고전 인스턴스)와 사실상 "
        "동형이라 검색·암기로 바로 풀리는가. 도메인 위장(은닉)이 무력할 정도의 "
        "표면 유사성을 당신의 지식으로 판단한다 (외부 DB 조회 없음). 특히 "
        "**제목·지문이 reduction_core(숨은 알고리즘) 이름이나 해법 자료구조·전략을 "
        "직접 드러내면** 은닉이 즉시 깨지므로 blocker (예: 제목 '다익스트라 최단경로')."
    ),
    "difficulty": (
        "난이도 일관성 — **명백한 모순만** 본다: 사실상 퇴화해 trivial 하게 풀리거나 "
        "(예: 입력 무관 상수 출력), 명세상 불가능한 요구. 난이도 측정/calibration 은 "
        "범위 밖."
    ),
    # N10e presentation — 상용 저지(BOJ) 수준 작문 게이트. 다른 kind 가 문체를 못
    # 보게 막는 규율(시스템 프롬프트)의 보완 — 문체·구성은 이 관점의 전담이다.
    "presentation": (
        "지문 품질 — 상용 저지(BOJ) 수준의 작문인가. 자연스러운 한국어 문어체인지, "
        "번역투/기계문체는 없는지, 용어 일관성(같은 대상을 다른 명칭으로 부르지 "
        "않는지), 문단 구성(도입→규칙→질문)이 서 있는지, 시나리오와 질문이 논리적으로 "
        "연결되는지, 정보 없는 잡설이나 과도한 압축은 없는지를 본다. blocker 는 "
        "**작문 자체가 무너져 전달이 실패하는 수준**(성립하지 않는 문장, 용어 붕괴, "
        "구성 파탄)만 — '해석이 여러 가지로 갈리는가'(중의성)는 ambiguity 관점의 "
        "전담이므로 이 관점에서 blocker 로 다루지 않는다. 문체 개선 여지는 "
        "warning/info 로 남긴다."
    ),
}

# 초급(is_basic) 문제 전용 완화 difficulty charter — 난이도-agnostic 원칙을 코드화.
# 표준 charter 의 'trivial 하게 풀리거나' 가 입문 문제를 '쉽다'는 이유로 reject 하던 것
# (단순 곱셈·분기 하나·엣지 적음)을 차단. **진짜 퇴화(상수출력/모순)만** blocker.
_DIFFICULTY_CHARTER_EASY = (
    "난이도 일관성 (초급 문제) — 이 문제는 **입문자용 기초 문제**다. 단순하고 쉬운 것은 "
    "**결함이 아니다**(의도된 난이도). '너무 쉽다/단순하다/한 연산으로 풀린다/엣지 케이스가 "
    "적다'는 이유로 막지 말 것. **오직 진짜 퇴화만** blocker 로 본다: 입력과 무관한 상수 "
    "출력(어떤 입력이든 같은 답), 명세상 불가능하거나 자기모순인 요구. 그 외 단순함은 "
    "통과시킨다. 난이도 측정/calibration 은 범위 밖."
)

# 초급(is_basic) 문제 전용 완화 presentation charter — narrative 의 초급 트랙은
# **의도적으로** 1~2문단 교과서체(입문 상용 수준)를 쓰고, abstract 도메인 센티널이면
# 시나리오 없이 맨 수식/IO 로 직접 서술한다. 표준 charter 의 '문단 구성(도입→규칙→질문)'·
# '시나리오-질문 연결'·'과도한 압축' 요구가 이 의도된 형식을 결함으로 오탈락시키는 것을
# 차단 (_DIFFICULTY_CHARTER_EASY 와 같은 false-reject 방지 규율의 presentation 판).
_PRESENTATION_CHARTER_EASY = (
    "지문 품질 (초급 문제) — 이 문제는 **입문자용 기초 문제**로, 짧은 1~2문단·시나리오 "
    "없는 맨 수식/IO 직접 서술이 **의도된 형식**이다. 간결함·시나리오 부재·도입 문단 "
    "부재는 **결함이 아니다** — 그 이유로 막지 말 것. 자연스러운 한국어 문어체인지, "
    "번역투/기계문체는 없는지, 용어 일관성(같은 대상을 다른 명칭으로 부르지 않는지)만 "
    "본다. **의미 전달·해석을 해치는 결함만 blocker**, 문체 개선 여지는 warning/info 로 "
    "남긴다."
)

# 초급(is_basic) 완화 charter 를 갖는 kind 의 dispatch 테이블 — 여기 없는 kind 는
# 표준 charter 그대로. 완화는 false-reject 방지 목적에 한정 (표준 charter 불변).
_EASY_CHARTERS: dict[QAReviewerKind, str] = {
    "difficulty": _DIFFICULTY_CHARTER_EASY,
    "presentation": _PRESENTATION_CHARTER_EASY,
}

_SYSTEM_PROMPT_TEMPLATE = """\
당신은 코딩테스트 문제 패키지의 QA 리뷰어다. 당신의 관점:
{charter}

typed QAReview (구조화된 tool call) 로 반환:
- kind: '{kind}' 그대로 (node 가 어차피 강제).
- passed: 이 관점에서 출하 가능하면 true.
- findings: 지적 사항 list (severity: info/warning/blocker). **blocker 가 하나라도
  있으면 passed=false** (모순 금지 — schema 가 reject 한다).
- rationale: 판정 근거 한 줄.

규율: 오직 위 관점만 판정한다 — 다른 관점(문체, 다른 결함 종류)은 지적하지 말 것.
사소한 개선 의견은 info/warning, 출하를 막아야 할 결함만 blocker.
"""


# 프롬프트 크기 바운드 상수 — 샘플/채점셋 노출은 리뷰 정밀도를 올리지만 (특히
# ambiguity 의 동률·중복 판단), 대형 케이스를 그대로 넣으면 토큰 폭발 → 절단.
_MAX_SAMPLES = 3  # 노출 샘플 수 상한 (기존 2 → 3: 경계 케이스 판독 근거 확대)
_SAMPLE_TEXT_LIMIT = 300  # 샘플 in/expected 필드당 문자 상한 (초과분 절단 표기)
_LARGE_CASE_RATIO = 0.5  # '대형 케이스' 판정: 최대 입력 크기 대비 비율


def _clip(text: str, limit: int = _SAMPLE_TEXT_LIMIT) -> str:
    """프롬프트 노출용 절단 — 초과분은 '…(+N자)' 로 표기해 크기만 전달."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…(+{len(text) - limit}자)"


def _build_user_prompt(state: V2State) -> str:
    bp = state.blueprint
    spec = state.spec
    nar = state.narrative
    suite = state.test_suite
    if spec is None or nar is None or suite is None:
        msg = "qa_reviewer requires state.spec, state.narrative, state.test_suite"
        raise ValueError(msg)
    categories = Counter(c.category for c in suite.cases)
    samples = "\n".join(
        f"  in={_clip(tc.input_text)!r} expected={_clip(tc.expected_output)!r}"
        for tc in spec.sample_testcases[:_MAX_SAMPLES]
    )
    # 최대 크기 케이스 요약 (입력 원문은 미노출 — 크기 신호만): 채점셋이 제약 상한
    # 근처를 실제로 커버하는지(스트레스 케이스 존재 여부/개수) 리뷰 근거 제공.
    sizes = [len(c.input_text) for c in suite.cases]
    max_size = max(sizes)
    biggest = suite.cases[sizes.index(max_size)]
    large_count = sum(1 for s in sizes if s >= max_size * _LARGE_CASE_RATIO)
    hidden = (
        [
            f"reduction_core (숨은 알고리즘): {bp.reduction_core.value}",
            f"composition: {[a.value for a in bp.composition]}",
            f"domain: {bp.domain}",
        ]
        if bp is not None
        else ["(blueprint 없음)"]
    )
    return "\n".join(
        [
            "[숨은 설계 — 판단 참고용, solver 는 볼 수 없음]",
            *hidden,
            "",
            "[solver 가 보는 문제 패키지]",
            f"title: {spec.title}",
            f"description:\n{spec.description}",
            f"input_format: {spec.io_contract.input_format}",
            f"output_format: {spec.io_contract.output_format}",
            "constraints: "
            + (
                ", ".join(format_constraint(c) for c in spec.constraints)
                or "(미명시)"
            ),
            f"samples (앞 {_MAX_SAMPLES}개):\n{samples}",
            "",
            "[채점셋 상세]",
            f"케이스 {len(suite.cases)}개, 카테고리 분포: {dict(categories)}",
            (
                f"최대 입력 크기: {max_size}자 (category={biggest.category}), "
                f"대형 케이스(최대의 {int(_LARGE_CASE_RATIO * 100)}%+ 크기) "
                f"{large_count}개"
            ),
        ]
    )


class QAReviewerLLM(Protocol):
    """qa_reviewer 의 LLM dependency. test 가 mock 주입."""

    def review(self, state: V2State, *, kind: QAReviewerKind) -> QAReview: ...


class AnthropicQAReviewerLLM:
    """production impl — Haiku + structured output, kind 별 charter 프롬프트."""

    def __init__(
        self, kind: QAReviewerKind, model: str = QA_REVIEWER_MODEL
    ) -> None:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.prompts import ChatPromptTemplate

        self._kind = kind
        llm = ChatAnthropic(model_name=model, timeout=60, stop=None)
        system = _SYSTEM_PROMPT_TEMPLATE.format(charter=_CHARTERS[kind], kind=kind)
        prompt = ChatPromptTemplate.from_messages(
            [("system", system), ("user", "{user}")]
        )
        self._chain = (prompt | llm.with_structured_output(QAReview)).with_retry(
            stop_after_attempt=5, wait_exponential_jitter=True
        )
        # 완화 charter 보유 kind(difficulty/presentation) 전용 초급 체인 — review 시
        # is_basic 이면 사용. 그 외 kind 는 표준 charter 그대로(동일 체인, 미사용)
        # → byte-identical.
        easy_charter = _EASY_CHARTERS.get(kind, _CHARTERS[kind])
        easy_system = _SYSTEM_PROMPT_TEMPLATE.format(charter=easy_charter, kind=kind)
        easy_prompt = ChatPromptTemplate.from_messages(
            [("system", easy_system), ("user", "{user}")]
        )
        self._chain_easy = (
            easy_prompt | llm.with_structured_output(QAReview)
        ).with_retry(stop_after_attempt=5, wait_exponential_jitter=True)

    def review(self, state: V2State, *, kind: QAReviewerKind) -> QAReview:
        # 초급(is_basic) + 완화 charter 보유 kind 만 완화 체인 — difficulty 는
        # '쉽다'고, presentation 은 '간결/시나리오 없다'고 막지 않는다.
        chain = (
            self._chain_easy
            if self._kind in _EASY_CHARTERS and is_basic(state.seed_algorithm)
            else self._chain
        )
        result = chain.invoke({"user": _build_user_prompt(state)})
        if not isinstance(result, QAReview):
            msg = (
                f"with_structured_output 가 {type(result).__name__} 반환 — "
                "QAReview 기대"
            )
            raise TypeError(msg)
        return result


def make_qa_reviewer_node(
    llm: QAReviewerLLM | None = None,
    *,
    kind: QAReviewerKind,
) -> Callable[[V2State], dict[str, Any]]:
    """factory — kind 관점 QA 리뷰어 노드 (N10a-e 공용). test 는 mock 주입.

    병렬 fan-out 노드라 partial dict 만 반환 (reducer 채널 누적). review.kind 는
    node 의 kind 로 강제 스탬프 — LLM 이 관점 라벨을 못 바꾼다.
    """
    resolved_llm: QAReviewerLLM = (
        llm if llm is not None else AnthropicQAReviewerLLM(kind)
    )

    def node(state: V2State) -> dict[str, Any]:
        if state.spec is None or state.narrative is None or state.test_suite is None:
            msg = "qa_reviewer requires state.spec, state.narrative, state.test_suite"
            raise ValueError(msg)
        review = resolved_llm.review(state, kind=kind)
        stamped = review.model_copy(update={"kind": kind})
        return {"qa_reviews": [stamped]}

    return node
