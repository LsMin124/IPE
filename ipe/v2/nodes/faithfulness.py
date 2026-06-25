"""Faithfulness 노드 — narrative round-trip 충실성 검증 (M3 step4).

LLM: Opus 4.7 (엄밀 의미 비교). blueprint-first 의 사후 게이트: 은닉 렌더된
``narrative`` 를 **재형식화**(round-trip)해 frozen ``blueprint`` 의 io 계약과 의미가
일치하는지 판정한다.

판정 규율 (RFC §5): 정보 *은닉*(알고리즘/기법 누락)은 distortion 이 **아니다**;
정보 *왜곡*(다른 io 형식·출력 의미·불변식 모순)만 reject. ``faithful=False`` 면 graph
가 narrative 재생성(싼 반복) — 그 라우팅은 step5 그래프 책임, 본 노드는 report 만 emit.

충실성은 **io 계약**(inputs/output_type/output_format/invariants) 중심으로 본다.
``reduction_core``(숨은 알고리즘)는 프롬프트에서 제외 — 은닉 모드에서 알고리즘 복원
가능성이 아니라 **형식 충실성**을 봐야 하므로 (anchoring·오판 회피).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ipe.v1.schema import NarrativeFaithfulnessReport

from ..backbone import resolve_backbone
from ..state import V2State

FAITHFULNESS_MODEL = "claude-opus-4-8"
FAITHFULNESS_TEMPERATURE = 0.2  # 엄밀 판정 (발산 금지)


_SYSTEM_PROMPT = """\
당신은 algorithmic problem faithfulness auditor 다. 은닉 렌더된 문제 지문(narrative)이
원래의 **동결된 형식 계약**(io_schema + output_invariants)을 왜곡 없이 표현하는지 round
-trip 으로 검증한다.

절차:
1. **먼저 narrative 만 읽고** 그 지문이 함의하는 입출력 계약(입력 구조/타입, 출력 형식·
   의미, 출력이 만족할 성질)을 머릿속으로 재형식화한다.
2. 그 재형식화를 주어진 frozen 계약(io_schema/output_invariants)과 비교한다.

typed NarrativeFaithfulnessReport (구조화된 tool call) 로 반환:
- faithful: 재형식화가 frozen 계약과 의미상 일치하면 true.
- distortions: 불일치(왜곡) 근거를 사람이 읽는 문장 list 로. faithful=true 면 빈 list.

판정 규율 (중요):
- **정보 은닉은 distortion 이 아니다**: 알고리즘 이름·풀이 기법·세부 힌트의 *누락*은
  정상(은닉 렌더의 목적). 이것으로 faithful=false 를 주지 말 것.
- **정보 왜곡만 reject**: narrative 가 frozen 계약과 *모순*되는 입출력 형식/범위/출력
  의미/불변식을 기술하면 distortion. 예: 출력이 '최단거리'인데 지문은 '최댓값'을 요구,
  입력이 정수 배열인데 지문은 문자열을 받음, 불변식(비음수)을 깨는 출력을 요구.
- **계약에 없는 데이터를 요구하는 메커니즘도 distortion 이다**: 지문이 기술한 규칙을
  적용하려면 frozen 계약에 *존재하지 않는* 입력 데이터가 필요한 경우 (예: '임계값
  미만 파이프는 사용 금지'라는데 io_schema 에 파이프별 용량 필드가 없음 → 주어진
  입력만으로 풀 수 없다). 은닉(누락)의 역방향 — 지문이 계약보다 *더 많은* 입력을
  전제하면 reject.
- **그래프 구조 사실 모순도 distortion 이다**: '구조 사실'로 주어진 directed(단방향/
  양방향)·self-loop·다중 간선·연결성과 narrative 가 **모순**되면 왜곡이다 (예: 구조 사실
  directed=단방향인데 지문이 '양방향으로 오갈 수 있다'; self-loop 없음인데 '자기 자신으로
  가는 길'을 서술). 구조 사실은 형식 계약의 일부 — 누락(은닉)은 OK 이나 **모순 서술은
  reject**. 구조 사실 섹션이 없으면(비-graph) 해당 없음.
- 모호하지만 모순은 아닌 경우(은닉으로 인한 일반화)는 faithful=true.
"""


def _build_user_prompt(state: V2State) -> str:
    narrative = state.narrative
    bp = state.blueprint
    if narrative is None or bp is None:
        msg = "faithfulness requires state.narrative and state.blueprint"
        raise ValueError(msg)
    invariants = [f"{iv.kind}: {iv.description}" for iv in bp.output_invariants]
    inputs = [
        f"{f.name}:{f.type}"
        + (f" size{f.size_range.min_value}..{f.size_range.max_value}" if f.size_range else "")
        + (f" val{f.value_range.min_value}..{f.value_range.max_value}" if f.value_range else "")
        for f in bp.io_schema.inputs
    ]
    mode = "hidden (은닉 — 알고리즘 누락 정상)" if narrative.hidden else "direct"
    structural = resolve_backbone(bp.io_schema).structural_facts(bp.io_schema)
    parts = [
        f"render mode: {mode}",
        f"domain: {bp.domain}",
        "",
        "[narrative — 먼저 이것만으로 io 계약을 재형식화]",
        narrative.scenario,
        "",
        "[frozen 형식 계약 — 위 재형식화와 비교]",
        f"io_schema.inputs: {inputs}",
        f"io_schema.output_type: {bp.io_schema.output_type}",
        f"io_schema.output_format: {bp.io_schema.output_format}",
        f"output_invariants: {invariants}",
    ]
    if structural:  # backbone 구조 사실(graph/sequence) — narrative 가 모순되면 distortion
        parts.extend(["", "[구조 사실 — narrative 가 이와 모순되면 왜곡]", *structural])
    return "\n".join(parts)


class FaithfulnessLLM(Protocol):
    """Faithfulness 의 LLM dependency. test 가 mock 주입."""

    def assess(self, state: V2State) -> NarrativeFaithfulnessReport: ...


class AnthropicFaithfulnessLLM:
    """production impl — Opus + structured output. lazy import (test 는 mock)."""

    def __init__(self, model: str = FAITHFULNESS_MODEL) -> None:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.prompts import ChatPromptTemplate

        llm = ChatAnthropic(model_name=model, timeout=60, stop=None)
        prompt = ChatPromptTemplate.from_messages(
            [("system", _SYSTEM_PROMPT), ("user", "{user}")]
        )
        self._chain = (
            prompt | llm.with_structured_output(NarrativeFaithfulnessReport)
        ).with_retry(stop_after_attempt=5, wait_exponential_jitter=True)

    def assess(self, state: V2State) -> NarrativeFaithfulnessReport:
        result = self._chain.invoke({"user": _build_user_prompt(state)})
        if not isinstance(result, NarrativeFaithfulnessReport):
            msg = (
                f"with_structured_output 가 {type(result).__name__} 반환 — "
                "NarrativeFaithfulnessReport 기대"
            )
            raise TypeError(msg)
        return result


def make_faithfulness_node(
    llm: FaithfulnessLLM | None = None,
) -> Callable[[V2State], V2State]:
    """factory — narrative + blueprint → 충실성 report. test 는 mock 주입.

    report 만 emit (faithful=False 시 narrative 재생성 라우팅은 step5 그래프 책임).
    """
    resolved_llm: FaithfulnessLLM = (
        llm if llm is not None else AnthropicFaithfulnessLLM()
    )

    def node(state: V2State) -> V2State:
        if state.narrative is None or state.blueprint is None:
            msg = "faithfulness requires state.narrative and state.blueprint"
            raise ValueError(msg)
        report = resolved_llm.assess(state)
        return state.model_copy(update={"faithfulness": report})

    return node
