"""sample_explainer 노드 — 완성 샘플(입력/정답)에 BOJ 표준 '예제 설명' 저작.

LLM: Sonnet 4.6, temperature 0.2 (사실 서술 — 창작 발산 금지). ``SampleTestCase.
description`` 은 스키마에 있으나 채우는 노드가 없어 영구 빈 문자열이던 채널 —
sample_filler 가 canonical golden 실행으로 expected 를 채운 **직후**가 유일한 안전
저작 지점이다(그 전엔 정답이 없어 설명이 지어낸 사실이 된다). 각 sample 의
``description`` 을 model_copy 로 채운다 (input/expected 불변).

은닉 규율(narrative 와 동일 계열): 설명은 '이 입력에서 왜 이 출력이 나오는지'
**인스턴스 수준** 사실만 — 알고리즘/자료구조/해법 전략 언급 금지.

**의도적 완화 설계**: 예제 설명은 장식적 품질 채널이라, LLM 예외 시 raise 하는
기존 노드들(narrative/formalizer 등)과 달리 **state 무변경 반환**한다 — 검증까지
통과한 패키지를 설명 저작 실패로 죽이면 안 된다. 개수 불일치도 min 길이까지만
채우고 나머지 sample 은 원본 유지(방어적 — 파이프라인 생존 우선).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ipe.v1.schema import SampleExplanations

from ..state import V2State

SAMPLE_EXPLAINER_MODEL = "claude-sonnet-4-6"
SAMPLE_EXPLAINER_TEMPERATURE = 0.2  # 인스턴스 사실 서술 (발산 금지, Faithfulness 와 동급)

# user prompt 필드별 절단 바운드 — 대형 scenario/sample 로 프롬프트 폭주 방지.
_FIELD_HEAD = 500


_SYSTEM_PROMPT = """\
당신은 코딩 문제의 '예제 설명' author 다. 완성된 샘플(입력/정답)마다 1~3문장의
한국어 설명을 쓴다 — 이 입력에서 왜 이 출력이 나오는지 **그 인스턴스 수준으로만**
설명한다 (BOJ 예제 설명 표준).

typed SampleExplanations (구조화된 tool call) 로 반환 — explanations 는 샘플 순서
그대로, **개수는 샘플 개수와 정확히 같게**.

규율:
- **알고리즘/자료구조/일반 해법 전략 언급 절대 금지** (은닉 규율): '최단 경로를
  다익스트라로 구한다' 같은 해법 처방을 쓰지 말 것 — '1→3→4 경로의 총 비용 7이
  가장 작다'처럼 **그 인스턴스의 사실만** 서술한다. 일반화된 풀이 절차·기법
  이름·자료구조는 어떤 형태로도 드러내지 않는다.
- **입력 형식 재서술 금지**: 줄 구성/순서/개수('첫째 줄의', '두 번째 수는'),
  인덱싱 규약 언급 금지 — 값의 **의미**만 쓴다 (형식의 진실원천은 입력 형식
  섹션이다).
- 지문(scenario)의 도메인 용어를 **그대로** 사용해 일관성을 유지한다 — 지문이
  '창고'라 부르면 설명도 '창고'(용어 드리프트 금지).
- 퇴화 샘플(도달 불가/시작==끝/해 없음 등)은 그 **의미**를 명시한다
  (예: '도달할 수 없으므로 -1 이다', '출발지와 목적지가 같아 비용은 0 이다').
- **확실하지 않은 사실을 지어내지 말 것** — 왜 그 출력이 나오는지 확신이 없으면
  해당 샘플 설명은 결과 서술만으로 짧게 끝낸다 (틀린 근거가 빈 근거보다 나쁘다).
"""


def _head(text: str, limit: int = _FIELD_HEAD) -> str:
    """prompt 렌더용 절단 — 바운드 초과분은 말줄임 (필드별 폭주 방지)."""
    return text if len(text) <= limit else text[:limit] + "…"


def _build_user_prompt(state: V2State) -> str:
    spec = state.spec
    narrative = state.narrative
    if spec is None or narrative is None:
        msg = "sample_explainer requires state.spec + state.narrative"
        raise ValueError(msg)
    parts = [
        "[지문 scenario — 도메인 용어 일관성 참고용 컨텍스트]",
        _head(narrative.scenario),
    ]
    bp = state.blueprint
    if bp is not None and bp.output_invariants:
        invariants = [f"{iv.kind}: {iv.description}" for iv in bp.output_invariants]
        parts.extend(["", f"output_invariants: {invariants}"])
    parts.extend(
        [
            "",
            f"샘플 {len(spec.sample_testcases)}개 — explanations 도 정확히 이 개수로.",
        ]
    )
    for i, sample in enumerate(spec.sample_testcases):
        parts.extend(
            [
                "",
                f"[샘플 {i}]",
                f"input_text: {_head(sample.input_text)}",
                f"expected_output: {_head(sample.expected_output)}",
            ]
        )
    return "\n".join(parts)


class SampleExplainerLLM(Protocol):
    """sample_explainer 의 LLM dependency. test 가 mock 주입."""

    def explain(self, state: V2State) -> SampleExplanations: ...


class AnthropicSampleExplainerLLM:
    """production impl — Sonnet + structured output. lazy import (test 는 mock)."""

    def __init__(self, model: str = SAMPLE_EXPLAINER_MODEL) -> None:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.prompts import ChatPromptTemplate

        llm = ChatAnthropic(model_name=model, timeout=60, stop=None)
        prompt = ChatPromptTemplate.from_messages(
            [("system", _SYSTEM_PROMPT), ("user", "{user}")]
        )
        self._chain = (
            prompt | llm.with_structured_output(SampleExplanations)
        ).with_retry(stop_after_attempt=5, wait_exponential_jitter=True)

    def explain(self, state: V2State) -> SampleExplanations:
        result = self._chain.invoke({"user": _build_user_prompt(state)})
        if not isinstance(result, SampleExplanations):
            msg = (
                f"with_structured_output 가 {type(result).__name__} 반환 — "
                "SampleExplanations 기대"
            )
            raise TypeError(msg)
        return result


def make_sample_explainer_node(
    llm: SampleExplainerLLM | None = None,
) -> Callable[[V2State], V2State]:
    """factory — 완성 샘플에 예제 설명 저작, sample.description 을 model_copy 로 채움.

    sample_filler(expected 확정) 후·edge_filler 전 배선 전제. spec/narrative 없으면
    방어적 no-op. **LLM 예외는 광범위 캐치 후 state 무변경 반환** — 예제 설명은
    장식적 품질 채널이라 파이프라인을 죽이지 않는다(기존 노드들의 raise 정책과
    다른 의도적 선택, 모듈 docstring 참조). 개수 불일치 시 min 길이까지만 채움.
    """
    resolved_llm: SampleExplainerLLM = (
        llm if llm is not None else AnthropicSampleExplainerLLM()
    )

    def node(state: V2State) -> V2State:
        spec = state.spec
        if spec is None or state.narrative is None:
            return state  # 상류 미완(방어적) — no-op
        try:
            result = resolved_llm.explain(state)
        except Exception:  # noqa: BLE001 — 의도적 광범위 캐치 (아래 이유)
            # 예제 설명은 장식적 품질 채널: 여기까지 온 state 는 golden 합의 +
            # 검증 경로를 통과한 자산이라, 설명 저작 실패(LLM 장애/파싱 실패/
            # retry 소진)로 전체 파이프라인을 죽이는 것이 설명 없는 출하보다
            # 훨씬 나쁘다. state 무변경 반환 — description 은 빈 문자열 유지.
            return state
        # 개수 불일치 시 min 길이까지만 채움 (zip 이 짧은 쪽에서 멈춤) — 나머지
        # sample 은 원본 유지(길이 보존, ProblemSpec min 3 제약 안전).
        filled = [
            sample.model_copy(update={"description": explanation})
            for sample, explanation in zip(
                spec.sample_testcases, result.explanations, strict=False
            )
        ]
        filled.extend(spec.sample_testcases[len(filled) :])
        new_spec = spec.model_copy(update={"sample_testcases": filled})
        return state.model_copy(update={"spec": new_spec})

    return node
