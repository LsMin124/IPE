"""coder 노드 — ProblemSpec + AlgorithmDesign + IterationContext → SolutionAttempt.

LLM: Opus 4.7. ``with_structured_output(SolutionAttempt)`` — typed deserialize.

D안 핵심:
- H1 (structured routing): prev verification.feedback (target_node enum +
  blocking_signature) 를 prompt 에 JSON-형식으로 명시 → fix 방향이 결정론적.
- H3 (skill amnesia 완화): IterationContext.accumulated_lessons + failed_strategies
  를 prompt 에 누적. signature dedup 으로 같은 lesson 반복 회피.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Protocol

from ..schema import SolutionAttempt
from ..state import V1State

CODER_MODEL = "claude-opus-4-7"
CODER_TEMPERATURE = 0.2


_SYSTEM_PROMPT = """\
당신은 expert competitive programmer 다. 주어진 ProblemSpec + AlgorithmDesign 에
대해 typed SolutionAttempt (구조화된 tool call) 를 반환한다.

요구사항:
- code: 완전한 python solution (stdin 으로 input 읽고 stdout 으로 output 출력).
- language: "python" (Phase 1 기본).
- iteration: 입력으로 받은 iteration 값.
- lessons: 이 시도에서 학습한 lessons 의 list (signature + content + from_iter).
  prev IterationContext 의 lessons 와 중복되지 않게 (signature dedup 의무).
- brute_code: optional — small N stress 용 naive O(N^2)/O(2^N) impl. 있으면 R15
  cross-check anchor.

prev verification.feedback 가 있으면:
- failure_mode + invariant_violations + actionable_hint 를 정확히 반영해 새 code.
- 같은 blocking_signature 반복 회피 (oscillation 방지).
- accumulated_lessons 와 failed_strategies 를 참고해 같은 실수 회피.
"""


# brute(검산자) 전용 system prompt — golden 과의 **프롬프트 격리**가 목적 (W2A).
# P2(합성·은닉)는 symbolic verifier off → golden×K vs brute reconcile 합의가 유일한
# 정답성 게이트인데, brute 가 golden 과 동일 prompt + 동일 AlgorithmDesign.pseudocode
# 를 소비하면 설계 오해 시 전 후보가 같은 오답에 합의('검증됨' 오출하). 따라서 brute 는
# 지문(ProblemSpec)만으로 독립 작성하고 설계를 읽지 않는다 — 독립 differential 복원.
# 요구사항(code/language/iteration/lessons 형식)은 _SYSTEM_PROMPT 와 동일하게 유지.
_BRUTE_SYSTEM_PROMPT = """\
당신은 신중한 검산자(reference checker)다. 주어진 문제 지문(ProblemSpec)만으로
**가장 단순하고 명백히 옳은** 정공법 풀이를 독립 작성해 typed SolutionAttempt
(구조화된 tool call) 를 반환한다. 효율은 무시하라 — 완전탐색/직접 시뮬레이션/O(N²)
이상도 좋다 (작은 검증 입력에만 실행된다). 제공되는 AlgorithmDesign(의사코드/복잡도)은
**읽지 말고 따르지도 말 것** — 당신의 역할은 그 설계와 독립적인 교차검증이다.

요구사항:
- code: 완전한 python solution (stdin 으로 input 읽고 stdout 으로 output 출력).
- language: "python" (Phase 1 기본).
- iteration: 입력으로 받은 iteration 값.
- lessons: 이 시도에서 학습한 lessons 의 list (signature + content + from_iter).
  prev IterationContext 의 lessons 와 중복되지 않게 (signature dedup 의무).

prev verification.feedback 가 있으면:
- failure_mode + invariant_violations + actionable_hint 를 정확히 반영해 새 code.
- 같은 blocking_signature 반복 회피 (oscillation 방지).
- accumulated_lessons 와 failed_strategies 를 참고해 같은 실수 회피.
"""


# v2 synthesis 전용 입력 파싱 규율 (opt-in). 골든/brute 코더가 같은 입력을 같게
# 읽어야 reconcile 합의 → 출하. 배치 진단상 array/value 시드 출하의 1위 병목이
# "양쪽 비-reference 코더 동시 IndexError"(파서 불일치)였다. literal 중괄호 금지
# (ChatPromptTemplate 변수 해석 — test_prompt_template_integrity 게이트).
_PARSE_DISCIPLINE = """
입력 파싱 규율 (골든·brute 가 같은 입력을 같게 읽어야 reconcile 합의 — 파서 불일치=RTE 거부):
- user 메시지에 '입력 파싱 preamble (필수)' 코드 블록이 주어지면, 그것을 **code 의 맨 앞에
  토씨 하나 바꾸지 말고 그대로** 두고, preamble 이 바인딩한 필드 변수로 알고리즘을 작성한다.
  **직접 stdin 파서를 작성하지 말 것** — preamble 이 형식의 단일 진실원천이다. 이로써 골든·
  brute 가 입력을 같게 소비해 IndexError(후행 스칼라 오소비)·중복카운트 오독이 사라진다.
- preamble 이 없을 때만(fallback) stdin 전체를 평탄 토큰 리스트로 읽어(data =
  sys.stdin.buffer.read().split()) io_contract.input_format 의 필드 순서대로만 소비한다.
"""


def _coder_system_prompt(
    parse_discipline: bool, *, brute_mode: bool = False
) -> str:
    """coder system prompt — ``parse_discipline`` 시 입력 파싱 규율을 append.

    v1 ``make_coder_node`` 는 기본 off 로 ``_SYSTEM_PROMPT`` 동결 유지(91.2% anchor
    자산); v2 synthesis(골든/brute)는 on 으로 파서 불일치 RTE(IndexError) 거부를 줄인다.
    ``brute_mode`` 는 base 를 ``_BRUTE_SYSTEM_PROMPT``(검산자 — 설계 비공유 독립
    교차검증)로 교체하되, ``_PARSE_DISCIPLINE`` append 는 동일 적용 — preamble 파싱
    규율은 reconcile 합의의 전제라 brute 에서도 절대 제거하지 않는다.
    """
    base = _BRUTE_SYSTEM_PROMPT if brute_mode else _SYSTEM_PROMPT
    if parse_discipline:
        return base + _PARSE_DISCIPLINE
    return base


# RTE 레버 — canonical 파서 기계적 보장 (fail_synthesis 1위 병목 54% 진단 후속, #2).
# prose 규율(_PARSE_DISCIPLINE)만으로는 약한 모델(골든=sonnet·brute=naive)이 preamble 을
# 안 따라 자기 파서를 써서 동일 입력에 IndexError → reconcile differ → fail_synthesis.
# preamble 은 결정적이라 '동일 입력+동일 preamble=동일 결과'(round-trip 게이트로 건전성
# 보증). 따라서 생성 후 preamble 포함을 결정적으로 검사하고, 누락 시 교정지시를 붙여
# 재생성한다(최대 N회). 끝까지 미준수면 마지막 출력을 best-effort 반환(=현행 동작, 무회귀).
_PARSE_COMPLIANCE_MAX_ATTEMPTS = 3

_PARSE_CORRECTIVE = (
    "교정 (필수): 직전 출력이 '입력 파싱 preamble' 을 누락하거나 변형했다. 이번에는 반드시 "
    "user 메시지의 'preamble' 코드 블록을 code 의 맨 앞에 토씨 하나 바꾸지 말고 그대로 "
    "복사하고, preamble 이 바인딩한 변수만으로 알고리즘을 작성하라 (직접 stdin 파서 작성 금지)."
)


def _normalize_code(code: str) -> str:
    """preamble 포함 비교용 — 공백/개행 무관(모델이 들여쓰기·줄바꿈을 살짝 바꿔도 동일 취급)."""
    return "".join(code.split())


def _parser_preamble_present(code: str, preamble: str) -> bool:
    """``preamble`` (공백 정규화) 이 ``code`` 에 그대로 포함됐는지. 빈 preamble 은 항상 True
    (v1 canonical 은 규율 비대상 — 무회귀)."""
    if not preamble:
        return True
    return _normalize_code(preamble) in _normalize_code(code)


def _coerce_parser_compliance(
    generate_once: Callable[[str | None], SolutionAttempt],
    preamble: str,
    *,
    max_attempts: int = _PARSE_COMPLIANCE_MAX_ATTEMPTS,
) -> SolutionAttempt:
    """preamble 미포함 시 교정지시를 붙여 재생성 — 최대 ``max_attempts``.

    ``generate_once(corrective)`` 는 교정문(없으면 None)을 받아 한 번 생성한다. preamble 을
    포함한 첫 출력을 반환하고, 끝까지 미준수면 마지막 출력을 best-effort 로 반환한다(현행
    동작 유지 = 무회귀; gate 는 합의율을 올릴 뿐 합성을 깨지 않는다).
    """
    last: SolutionAttempt | None = None
    for i in range(max_attempts):
        corrective = None if i == 0 else _PARSE_CORRECTIVE
        last = generate_once(corrective)
        if _parser_preamble_present(last.code, preamble):
            return last
    assert last is not None  # max_attempts >= 1 (호출자 보증)
    return last


def _render_lessons(state: V1State) -> str:
    lessons = state.context.accumulated_lessons
    if not lessons:
        return ""
    lines = ["accumulated_lessons (signature dedup 누적):"]
    for i, lesson in enumerate(lessons, start=1):
        lines.append(f"  {i}. [{lesson.signature}] {lesson.content}")
    return "\n".join(lines)


def _render_failed_strategies(state: V1State) -> str:
    strategies = state.context.failed_strategies
    if not strategies:
        return ""
    lines = ["failed_strategies (다음 시도에서 회피):"]
    for s in strategies:
        lines.append(
            f"  - [{s.signature}] {s.description} "
            f"(mode={s.failure_mode.value}, iter={s.occurred_at_iter})"
        )
    return "\n".join(lines)


def _render_prev_verification(state: V1State) -> str:
    v = state.verification
    if v is None or v.feedback is None:
        return ""
    payload = {
        "iteration": v.iteration,
        "failure_mode": v.failure_mode.value,
        "samples_engaged": v.samples_engaged,
        "invariant_violations": [
            {
                "invariant_kind": iv.invariant_kind,
                "description": iv.description,
                "evidence": iv.evidence,
            }
            for iv in v.invariant_violations
        ],
        "feedback": {
            "target_node": v.feedback.target_node.value,
            "actionable_hint": v.feedback.actionable_hint,
            "blocking_signature": v.feedback.blocking_signature,
        },
    }
    return (
        "prev verification (structured JSON):\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
    )


def _render_prev_attempt(state: V1State) -> str:
    if state.attempt is None:
        return ""
    return (
        "prev attempt code (수정 대상):\n```python\n"
        + state.attempt.code
        + "\n```"
    )


def _build_user_prompt(state: V1State, *, brute_mode: bool = False) -> str:
    """coder user prompt 렌더. ``brute_mode`` 시 design 관련 라인(algorithm/
    complexity_target/pseudocode/required invariants)을 **제외** — brute 검산자가
    골든의 설계 오해를 그대로 재생산하지 않는 독립 differential 의 프롬프트 격리.
    나머지(spec/샘플/preamble/lessons/verification)는 동일 렌더."""
    spec = state.spec
    design = state.design
    if spec is None or design is None:
        msg = "coder requires state.spec and state.design — earlier nodes must run first"
        raise ValueError(msg)

    parts = [
        f"target_algorithm: {state.target_algorithm.value}",
        f"iteration: {state.iteration}",
        "",
        f"problem title: {spec.title}",
        f"description: {spec.description}",
        f"io_contract.input_format: {spec.io_contract.input_format}",
        f"io_contract.output_format: {spec.io_contract.output_format}",
        f"time_limit_ms: {spec.time_limit_ms}, memory_limit_mb: {spec.memory_limit_mb}",
    ]
    if not brute_mode:
        parts += [
            "",
            f"algorithm: {design.algorithm_name}",
            f"complexity_target: time={design.complexity_target.time_big_o}, "
            f"space={design.complexity_target.space_big_o}",
            f"pseudocode: {design.pseudocode}",
            f"required invariants: {[i.kind for i in design.invariants]}",
        ]
    parts += [
        "",
        "sample testcases (input → expected):",
    ]
    for i, sample in enumerate(spec.sample_testcases):
        parts.append(f"  [{i}] input:\n{sample.input_text}")
        parts.append(f"      expected: {sample.expected_output!r}")

    if spec.input_parser_code:
        parts.append("")
        parts.append(
            "입력 파싱 preamble (필수 — code 맨 앞에 그대로 두고, 바인딩된 변수로 풀 것):\n"
            "```python\n" + spec.input_parser_code + "\n```"
        )

    for extra in (
        _render_lessons(state),
        _render_failed_strategies(state),
        _render_prev_verification(state),
        _render_prev_attempt(state),
    ):
        if extra:
            parts.append("")
            parts.append(extra)
    return "\n".join(parts)


class CoderLLM(Protocol):
    """coder 의 LLM dependency."""

    def generate(self, state: V1State) -> SolutionAttempt: ...


class AnthropicCoderLLM:
    """production impl — Opus + structured output."""

    def __init__(
        self,
        model: str = CODER_MODEL,
        *,
        parse_discipline: bool = False,
        brute_mode: bool = False,
    ) -> None:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.prompts import ChatPromptTemplate

        llm = ChatAnthropic(model_name=model, timeout=60, stop=None)
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    _coder_system_prompt(parse_discipline, brute_mode=brute_mode),
                ),
                ("user", "{user}"),
            ]
        )
        self._chain = (
            prompt | llm.with_structured_output(SolutionAttempt)
        ).with_retry(stop_after_attempt=5, wait_exponential_jitter=True)
        self._parse_discipline = parse_discipline
        self._brute_mode = brute_mode

    def generate(self, state: V1State) -> SolutionAttempt:
        preamble = ""
        if self._parse_discipline and state.spec is not None:
            preamble = state.spec.input_parser_code

        def _once(corrective: str | None) -> SolutionAttempt:
            user = _build_user_prompt(state, brute_mode=self._brute_mode)
            if corrective:
                user = f"{user}\n\n{corrective}"
            result = self._chain.invoke({"user": user})
            if not isinstance(result, SolutionAttempt):
                msg = (
                    f"with_structured_output 가 {type(result).__name__} 반환 — "
                    "SolutionAttempt 기대"
                )
                raise TypeError(msg)
            return result

        # parse_discipline + canonical preamble 일 때만 합의율 게이트 (그 외 현행=무회귀).
        if preamble:
            return _coerce_parser_compliance(_once, preamble)
        return _once(None)


def make_coder_node(
    llm: CoderLLM | None = None,
) -> Callable[[V1State], V1State]:
    resolved_llm: CoderLLM = llm if llm is not None else AnthropicCoderLLM()

    def node(state: V1State) -> V1State:
        if state.spec is None or state.design is None:
            msg = "coder requires state.spec and state.design"
            raise ValueError(msg)
        attempt = resolved_llm.generate(state)
        return state.model_copy(update={"attempt": attempt})

    return node
