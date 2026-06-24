"""Narrative 노드 — frozen ProblemBlueprint → Narrative 렌더 (M3 step3, late 렌더).

LLM: Sonnet 4.6 (창작적 시나리오). blueprint-first 의 **마지막** 모델링 단계: frozen
blueprint(io_schema/invariants/숨은 reduction_core)를 현실 도메인 시나리오로 렌더한다.
``hidden=True`` 면 알고리즘 은닉(B2B), ``False`` 면 직접 기술(B2C 토픽드릴).

freeze 규율(step2 와 동일): LLM 은 ``NarrativeDraft``(scenario 프로즈만) 산출 → 노드가
``hidden``(graph config) + ``domain``(blueprint carry-over) 스탬프 → 렌더 모드/도메인을
LLM 이 임의로 못 바꾼다. 충실성(왜곡 여부)은 step4 round-trip 이 사후 검증.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ipe.v1.schema import Narrative, NarrativeDraft

from ..generation.input_gen import render_structural_facts
from ..state import V2State

NARRATIVE_MODEL = "claude-sonnet-4-6"
NARRATIVE_TEMPERATURE = 0.7  # 창작적 다양성 (Strategist 와 동급, Formalizer 0.2 대비)


_SYSTEM_PROMPT = """\
당신은 algorithmic problem narrative author 다. 이미 **동결된** ProblemBlueprint
(입출력 형식 + 불변식 + 숨은 reduction_core)를 받아, 그 문제를 현실 도메인 시나리오
지문으로 렌더한다.

typed NarrativeDraft (구조화된 tool call) 로 반환 — scenario 프로즈만:
- scenario: 주어진 domain 의 현실 상황으로 문제를 서술한 지문. io_schema 와
  **의미적으로** 호환되어야 하며(무엇이 주어지고 무엇을 구하는지), output_invariants
  와 모순되지 않아야 한다.

렌더 모드:
- hidden=True (은닉, B2B): reduction_core(숨은 알고리즘) 이름을 **절대 언급하지 말 것**.
  domain 의 자연스러운 상황으로 위장하되, 형식적으로는 정확히 그 알고리즘으로 환원되게.
  solver 가 지문만 보고 어떤 표준 알고리즘인지 바로 알아채지 못해야 한다.
- hidden=False (직접, B2C): 알고리즘을 직접 명시해도 된다 (토픽 드릴 학습용).

규율:
- blueprint 의 입출력 형식/제약/불변식을 **왜곡하지 말 것** (정보 은닉은 OK, 왜곡은 금지).
  은닉=세부 누락, 왜곡=다른 문제 기술. 후자는 다음 단계(faithfulness)가 reject 한다.
- domain 은 blueprint 가 고정한 그대로 사용 (당신이 바꾸지 않는다).
- **입출력 '형식' 서술 금지**: 입력의 줄 구성/순서/개수, 인덱싱 규약(0-indexed 또는
  1-indexed), 변수 나열식 형식 정의, 구체 입력/출력 예시 블록을 scenario 에 쓰지
  말 것. 형식의 단일 진실원천은 이후 단계가 동결 렌더하는 '입력 형식' 섹션과 샘플
  테스트케이스다 — scenario 가 형식을 재서술하면 그 섹션과 인덱싱·순서 모순이 생겨
  QA 에서 reject 된다. scenario 는 상황과 의미만 서술한다. (위 io_schema 정보는
  이해를 위한 참고이지, 지문에 옮겨 적으라는 것이 아니다.)
- composition 이 있으면 시나리오의 **질문 자체**가 그 기법을 실제로 요구해야 한다
  (예: '조건을 만족하는 최소 임계값을 구하라' 처럼 반복 판정이 필요한 질문).
  기법 이름 언급은 여전히 금지(은닉) — 요구만 반영한다. reduction_core 만으로
  끝나는 질문에 합성 기법을 **장식**으로 끼워 넣지 말 것 (고전 동형 = QA 유출
  reject).
- **풀이 방법('어떻게 푸는지') 서술 절대 금지**: scenario 는 '무엇을 구하는지'(문제와
  출력)만 서술하고, 그것을 **어떻게** 계산하는지는 쓰지 말 것. 알고리즘 이름을 안
  써도 **자료구조·해법 전략을 처방**하면 풀이가 그대로 유출돼 QA leakage 게이트에서
  reject 된다 — 예: '좌표 압축한 뒤 구간 최댓값 쿼리를 지원하는 인덱스/세그먼트
  트리로 처리하라', '정렬 후 이분 탐색', '누적 카운팅', '메모이제이션 테이블',
  '~를 빠르게 조회·갱신하는 자료구조가 요구된다'. **효율성 요구는 제약(N 의 범위)으로만
  암시**하고, 해법 자료구조·기법·시간복잡도 전략은 지문에 언급하지 않는다.
- output_invariants 가 정의한 **퇴화/경계 케이스 동작**(시작==끝, 도달 불가,
  0 값·예산 하한, 중복/다중 간선 처리 등)은 지문에 **반드시 서술**한다 — 이것은
  금지된 '형식' 서술이 아니라 **출력 의미의 일부**다 (예: "출발지와 목적지가
  같으면 답은 0 이다", "도달할 수 없는 경우 -1 을 출력한다"). 퇴화 케이스 동작이
  지문에 없으면 solver 가 해석을 강요받는 모호 문제가 되어 QA 에서 reject 된다.
  단 **output_invariants 에 실제로 있는 케이스만** 서술한다 — 없는 동작을 지어내지 말 것.
- **그래프 구조 사실은 주어진 '구조 사실' 데이터와 일치하게 서술한다**: 아래 '구조 사실'
  섹션에 directed(단방향/양방향)·self-loop·다중 간선·연결성이 **데이터로** 주어진다.
  그 데이터대로 반영하고(예: directed=양방향이면 '양쪽으로 오갈 수 있다', self-loop 없음이면
  자기 간선을 언급하지 않음), 데이터와 **모순**되는 구조를 지어내지 말 것 — directed=단방향인데
  '양방향 도로'로, self-loop 없음인데 '자기 루프 처리'로 서술하면 faithfulness 가 reject
  한다(F8 모순 차단). 구조 사실 섹션이 비어 있으면(비-graph) 해당 없음.
- output_invariants 의 **답 유일성/동률 해소**(answer_uniqueness)도 퇴화 의미와
  마찬가지로 지문에 **의미 수준으로 서술**한다(형식 서술 아님 — 출력 의미의 일부):
  답이 유일하게 정해진다는 점과, 동률이 생길 수 있는 경우(같은 정렬 키, 복수 최적해,
  동일 값을 주는 서로 다른 후보) 무엇이 답인지를 의미로 밝힌다 (예: "답은 그 최대
  길이 자체이므로 어떤 체인을 고르든 같다", "정렬 기준이 같은 거래는 접수 순서를
  따른다"). 동률 처리가 지문에 없으면 solver 가 해석을 강요받아 QA ambiguity
  게이트에서 reject 된다.
"""


# back-route(B) 재진입 시 QA findings 렌더 바운드 — 지적 해소 방향 재작성을 유도하되
# 프롬프트 폭주 방지 (finding 은 심각도순 일부, 본문 truncate).
_QA_FEEDBACK_MAX_FINDINGS = 6
_QA_FEEDBACK_TEXT_HEAD = 200


def _qa_feedback_lines(state: V2State) -> list[str]:
    """직전 QA 실패 리뷰 findings → 재작성 지시 라인 (back-route 재진입에서만 비지 않음).

    첫 pass 는 qa_report=None, 통과 report 면 재진입 자체가 없으므로 빈 list —
    메인 경로의 prompt 는 불변. 실패 kind 의 blocker/warning 위주로 렌더.
    """
    report = state.qa_report
    if report is None or report.overall_pass:
        return []
    severity_rank = {"blocker": 0, "warning": 1, "info": 2}
    lines = ["[직전 QA 리뷰 실패 — 아래 지적을 해소하도록 scenario 를 재작성하라]"]
    shown = 0
    for review in report.reviews:
        if review.passed:
            continue
        lines.append(f"- {review.kind}: {review.rationale[:_QA_FEEDBACK_TEXT_HEAD]}")
        ordered = sorted(review.findings, key=lambda fd: severity_rank[fd.severity])
        for finding in ordered:
            if shown >= _QA_FEEDBACK_MAX_FINDINGS:
                break
            lines.append(
                f"  * [{finding.severity}] "
                f"{finding.description[:_QA_FEEDBACK_TEXT_HEAD]}"
            )
            shown += 1
    return lines


def _build_user_prompt(state: V2State, *, hidden: bool) -> str:
    bp = state.blueprint
    if bp is None:
        msg = "narrative requires state.blueprint — formalizer must run first"
        raise ValueError(msg)
    invariants = [f"{iv.kind}: {iv.description}" for iv in bp.output_invariants]
    inputs = [f"{f.name}:{f.type}" for f in bp.io_schema.inputs]
    parts = [
        f"render mode: {'hidden (은닉)' if hidden else 'direct (직접)'}",
        f"domain: {bp.domain}",
        f"reduction_core (숨은 알고리즘): {bp.reduction_core.value}",
        f"composition: {[a.value for a in bp.composition]}",
        "",
        f"io_schema.inputs: {inputs}",
        f"io_schema.output_type: {bp.io_schema.output_type}",
        f"io_schema.output_format: {bp.io_schema.output_format}",
        f"output_invariants: {invariants}",
    ]
    structural = render_structural_facts(bp.io_schema)
    if structural:  # graph_shape 핀된 graph 필드만 — narrative 가 이 DATA 와 일치 서술
        parts.extend(["", "[구조 사실 — 지문에 일치하게 서술 (모순 금지)]", *structural])
    feedback = _qa_feedback_lines(state)
    if feedback:
        parts.extend(["", *feedback])
    return "\n".join(parts)


class NarrativeLLM(Protocol):
    """Narrative 의 LLM dependency. test 가 mock 주입."""

    def render(self, state: V2State, *, hidden: bool) -> NarrativeDraft: ...


class AnthropicNarrativeLLM:
    """production impl — Sonnet + structured output. lazy import (test 는 mock)."""

    def __init__(self, model: str = NARRATIVE_MODEL) -> None:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.prompts import ChatPromptTemplate

        llm = ChatAnthropic(model_name=model, timeout=60, stop=None)
        prompt = ChatPromptTemplate.from_messages(
            [("system", _SYSTEM_PROMPT), ("user", "{user}")]
        )
        self._chain = (prompt | llm.with_structured_output(NarrativeDraft)).with_retry(
            stop_after_attempt=5, wait_exponential_jitter=True
        )

    def render(self, state: V2State, *, hidden: bool) -> NarrativeDraft:
        result = self._chain.invoke({"user": _build_user_prompt(state, hidden=hidden)})
        if not isinstance(result, NarrativeDraft):
            msg = (
                f"with_structured_output 가 {type(result).__name__} 반환 — "
                "NarrativeDraft 기대"
            )
            raise TypeError(msg)
        return result


def make_narrative_node(
    llm: NarrativeLLM | None = None,
    *,
    hidden: bool = True,
) -> Callable[[V2State], V2State]:
    """factory — frozen blueprint → Narrative 렌더. ``hidden`` 은 graph-time 렌더 모드.

    기본 ``hidden=True`` (B2B 은닉). ``domain`` 은 blueprint 에서 carry-over,
    ``hidden`` 은 이 인자에서 스탬프 → 렌더 모드/도메인을 LLM 이 못 바꾼다 (freeze 규율).
    """
    resolved_llm: NarrativeLLM = (
        llm if llm is not None else AnthropicNarrativeLLM()
    )

    def node(state: V2State) -> V2State:
        bp = state.blueprint
        if bp is None:
            msg = "narrative requires state.blueprint — formalizer must run first"
            raise ValueError(msg)
        draft = resolved_llm.render(state, hidden=hidden)
        narrative = Narrative(
            scenario=draft.scenario,
            hidden=hidden,
            domain=bp.domain,
        )
        return state.model_copy(update={"narrative": narrative})

    return node
