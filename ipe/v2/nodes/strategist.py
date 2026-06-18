"""Strategist 노드 — seed_algorithm hint → StrategySeed (M3 step2, blueprint-first).

LLM: Sonnet 4.6 (발산적 시드). 책임 = **무엇을 숨길지**(reduction_core) + 합성 기법
+ **위장 도메인**(domain) 결정. 형식 동결(io_schema/invariants)은 Formalizer(Opus)로
분리 — Q1 = 분리 확정.

``composition_mode`` (Phase 4 — P1/P2 수렴):
- ``"single"`` (P1) — composition **빈값 강제**. 단일 알고리즘 문제(합성 없음). 난이도·
  위장은 domain 시나리오와 형식 설계로만 확보.
- ``"composed"`` (P2, 기본) — composition **비움 금지**(≥1). reduction_core + 최소 1개
  결합으로 **총 2개 이상** 알고리즘/자료구조. 합성 후보 팔레트 안에서만 선택.

``with_structured_output(StrategySeed)`` 로 typed deserialize (prose parsing 없음).
factory/Protocol/lazy-Anthropic 패턴은 v1 architect 와 동일.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable
from typing import Literal, Protocol

from ipe.v1.schema import StrategySeed, TargetAlgorithm

from ..state import V2State

CompositionMode = Literal["single", "composed"]

STRATEGIST_MODEL = "claude-sonnet-4-6"
STRATEGIST_TEMPERATURE = 0.7  # 발산적 위장 다양성 (Formalizer 의 0.2 와 대비)

# enum 허용값을 prompt 에 명시 — 모델이 'greedy' 같은 목록 밖 자연어 기법을 emit 해
# structured output 검증이 retry 전부 실패하는 것을 방지 (M4 step5 e2e 실측 발견).
_VALID_ALGORITHMS = ", ".join(a.value for a in TargetAlgorithm)

# 매 run 제안하는 합성 후보 팔레트 크기. 19종 배치 실측상 composition 이 prose 규율에도
# fenwick(13/19)·sort(11/19)로 mode-collapse → 어휘 붕괴(=leakage 레버). run_id 로 시드된
# 결정적 회전 팔레트를 주입해 **제안되는 집합 자체**를 통제: fenwick 이 팔레트에 드는 run
# 이 ~PALETTE/(N-1) 로 제한되어 LLM 편향과 무관하게 어휘가 구조적으로 분산된다.
_COMPOSITION_PALETTE_SIZE = 7


def _composition_palette(run_id: str, reduction_core: TargetAlgorithm) -> list[str]:
    """run_id 로 시드한 결정적 합성 후보 팔레트 (reduction_core 제외, 회전).

    builtin ``hash()`` 는 PYTHONHASHSEED 의존이라 비결정 → sha256 로 안정 seed.
    같은 run_id→같은 팔레트(재현), run 마다 다른 부분집합→어휘 분산.
    """
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    candidates = [a.value for a in TargetAlgorithm if a != reduction_core]
    rng.shuffle(candidates)
    return sorted(candidates[:_COMPOSITION_PALETTE_SIZE])


# 매 run 제안하는 위장 도메인 팔레트 크기. DB 16문제 실측상 domain 이 strategist 자유
# 선택에서 'banking' 100% mode-collapse(composition fenwick 68% 보다 심함). composition
# 과 달리 domain 은 enum 이 아니라 자유 텍스트라 회전 후보 pool 을 명시 — run_id 로 시드된
# 결정적 회전 팔레트를 주입해 **제안되는 도메인 집합 자체**를 통제, banking 단일화를 구조적
# 으로 분산(leakage 방어 겸 — 동일 도메인 반복이 고전 동형 인식을 키운다).
_DOMAIN_PALETTE_SIZE = 7

# 위장 도메인 후보 pool — 알고리즘과 무관하게 시나리오를 호스팅할 수 있는 distinct 한
# 현실 도메인들. 'finance' 도 한 후보로 포함(banishing 아닌 분산 — over-correction 회피).
_DOMAIN_POOL: tuple[str, ...] = (
    "logistics",
    "healthcare",
    "telecommunications",
    "social-network",
    "genomics",
    "manufacturing",
    "energy-grid",
    "transportation",
    "e-commerce",
    "gaming",
    "astronomy",
    "agriculture",
    "urban-planning",
    "cybersecurity",
    "supply-chain",
    "robotics",
    "environmental-monitoring",
    "education",
    "media-streaming",
    "disaster-response",
    "finance",
)


def _domain_palette(run_id: str) -> list[str]:
    """run_id 로 시드한 결정적 위장 도메인 팔레트 (회전).

    composition 팔레트와 동형 — sha256 안정 seed. 같은 run_id→같은 팔레트(재현),
    run 마다 다른 부분집합→도메인 분산(banking mode-collapse 의 구조적 해소).
    seed 에 ``domain:`` prefix 를 두어 composition 팔레트 seed 와 분리한다.
    """
    seed = f"domain:{run_id}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    pool = list(_DOMAIN_POOL)
    rng.shuffle(pool)
    return sorted(pool[:_DOMAIN_PALETTE_SIZE])


# composition 모드별 지령 (Phase 4 — P1/P2 수렴). single=합성 금지 / composed=합성 필수.
_COMPOSITION_DIRECTIVE_SINGLE = """\
composition (단일 모드 — 합성 금지):
- 이 문제는 **단일 알고리즘 문제**다. composition 은 **반드시 빈 list** 로 둔다.
  reduction_core 하나만으로 문제를 구성하며, 추가 기법을 일절 합성하지 않는다.
  난이도·위장은 domain 시나리오와 형식 설계로만 확보한다 (억지 합성 금지)."""

_COMPOSITION_DIRECTIVE_COMPOSED = """\
composition 다양성 (유출 게이트 실측 반영 — 어휘 mode-collapse 방어):
- 이 문제는 **합성 문제**다. composition 은 **비우지 않는다** — reduction_core 외에
  최소 1개 기법을 결합해 **총 2개 이상**의 알고리즘/자료구조로 문제를 구성한다.
- reduction_core 의 표준 구현에 **이미 내장된** 기법은 composition 에 넣지 말 것
  (예: dijkstra 의 heap/우선순위 큐) — 합성 강제력이 없는 장식이라 문제를 바꾸지
  못한다.
- composition 은 **반드시 user 메시지의 '합성 후보 팔레트(이번 run)' 안에서만** 고른다.
  팔레트는 run 마다 회전하며, 한 기법(특히 fenwick/binary_search)에 고정되는 어휘
  붕괴를 막아 유출 게이트의 고전 동형 reject 를 줄인다. 팔레트에서 reduction_core 와
  **출력 의미를 바꾸며 자연스럽게 결합**되는 1~2개를 고른다 — 집계/계수, 전처리·정렬,
  누적 질의, 연결성 전처리 등. 팔레트에 꼭 맞는 게 없어도 **가장 자연스럽게 결합되는
  하나는 반드시 고른다**(단일 문제는 별도 모드라 여기선 비울 수 없다). 팔레트 밖 기법은
  쓰지 않는다."""


def _system_prompt(composition_mode: CompositionMode) -> str:
    """composition_mode 별 system prompt — single=합성 금지 / composed=합성 필수.

    composition 규율만 모드로 분기하고 나머지(enum 명시·domain 다양성·은닉 목표)는 공유.
    """
    composition_directive = (
        _COMPOSITION_DIRECTIVE_SINGLE
        if composition_mode == "single"
        else _COMPOSITION_DIRECTIVE_COMPOSED
    )
    return f"""\
당신은 algorithmic problem strategist 다. 주어진 target algorithm hint 를 받아,
그 알고리즘을 **은닉**한 코딩테스트 문제의 전략 시드를 설계한다.

typed StrategySeed (구조화된 tool call) 로 반환:
- reduction_core: 숨길 핵심 알고리즘 (보통 hint 와 동일하거나 그 환원). solver 가
  지문만 보고 바로 알아채지 못하게 할 대상.
- composition: reduction_core 외 합성할 추가 기법들. 아래 'composition' 규율을 따른다.
- domain: 현실 세계 도메인 (예: 'logistics', 'social-network', 'genomics'). 이
  도메인의 시나리오로 알고리즘이 자연스럽게 위장되어야 한다. **반드시 user 메시지의
  '도메인 팔레트(이번 run)' 안에서** 고른다 (아래 domain 다양성 규율).
- rationale: 이 위장이 왜 reduction_core 를 효과적으로 숨기는지 한 줄 근거.

reduction_core 와 composition 의 모든 원소는 **반드시 다음 값 중에서만** 골라야 한다:
{_VALID_ALGORITHMS}.
이 목록 밖 기법(예: 'greedy', 'two_pointers', 'sliding_window')은 schema 검증에서
거부된다 — 그런 기법을 섞고 싶으면 목록 내 가장 가까운 값을 쓴다.

{composition_directive}

domain 다양성 (DB 실측 반영 — banking 단일화 방어):
- domain 은 **반드시 user 메시지의 '도메인 팔레트(이번 run)' 안에서** 고른다. 팔레트는
  run 마다 회전하며, 특정 도메인(특히 'banking/finance')에 고정되는 단일화를 막아 동일
  도메인 반복이 키우는 고전 동형 인식(유출)을 줄인다. composition 과 달리 domain 은
  필수이므로 비울 수 없다 — 팔레트에서 reduction_core 시나리오를 **가장 자연스럽게
  위장**하는 하나를 고른다. 도메인 용어로 입출력이 자연스럽게 읽혀야 한다.

핵심 목표 (은닉): domain 시나리오만 읽고는 reduction_core 가 무엇인지 **바로 드러나지
않아야** 한다. 그러나 형식적으로는 정확히 그 알고리즘으로 환원되어야 한다. 형식 동결
(입출력 스키마/불변식)은 다음 단계(Formalizer)가 담당하므로 여기서는 전략만 정한다.
"""


def _build_user_prompt(state: V2State, composition_mode: CompositionMode) -> str:
    """run 팔레트 주입 user prompt. composed 면 합성 팔레트도, single 이면 도메인만."""
    dom_palette = _domain_palette(state.run_id)
    lines = [
        f"target algorithm hint: {state.seed_algorithm.value}",
        f"run_id: {state.run_id}",
        "",
    ]
    if composition_mode == "composed":
        comp_palette = _composition_palette(state.run_id, state.seed_algorithm)
        lines += [
            "합성 후보 팔레트 (이번 run — composition 은 이 안에서만 골라라):",
            ", ".join(comp_palette),
            "",
        ]
    lines += [
        "도메인 팔레트 (이번 run — domain 은 이 안에서 고른다):",
        ", ".join(dom_palette),
    ]
    return "\n".join(lines)


class StrategistLLM(Protocol):
    """Strategist 의 LLM dependency. test 가 mock 주입."""

    def seed(self, state: V2State) -> StrategySeed: ...


class AnthropicStrategistLLM:
    """production impl — Sonnet + structured output. lazy import (test 는 mock).

    ``composition_mode`` 가 system prompt(합성 금지/필수)와 user prompt(합성 팔레트
    주입 여부)를 결정한다 (Phase 4 — P1=single / P2=composed).
    """

    def __init__(
        self,
        model: str = STRATEGIST_MODEL,
        *,
        composition_mode: CompositionMode = "composed",
    ) -> None:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.prompts import ChatPromptTemplate

        self._composition_mode = composition_mode
        llm = ChatAnthropic(model_name=model, timeout=60, stop=None)
        prompt = ChatPromptTemplate.from_messages(
            [("system", _system_prompt(composition_mode)), ("user", "{user}")]
        )
        self._chain = (prompt | llm.with_structured_output(StrategySeed)).with_retry(
            stop_after_attempt=5, wait_exponential_jitter=True
        )

    def seed(self, state: V2State) -> StrategySeed:
        result = self._chain.invoke(
            {"user": _build_user_prompt(state, self._composition_mode)}
        )
        if not isinstance(result, StrategySeed):
            msg = (
                f"with_structured_output 가 {type(result).__name__} 반환 — "
                "StrategySeed 기대"
            )
            raise TypeError(msg)
        return result


def make_strategist_node(
    llm: StrategistLLM | None = None,
    *,
    composition_mode: CompositionMode = "composed",
) -> Callable[[V2State], V2State]:
    """factory — graph build 시 호출. test 는 mock LLM 주입.

    ``composition_mode`` 는 production LLM(llm=None)의 프롬프트만 좌우한다 — mock
    주입 경로는 mock 이 emit 하는 seed 가 진실(테스트가 single/composed 시드를 직접 지정).
    """
    resolved_llm: StrategistLLM = (
        llm
        if llm is not None
        else AnthropicStrategistLLM(composition_mode=composition_mode)
    )

    def node(state: V2State) -> V2State:
        seed = resolved_llm.seed(state)
        return state.model_copy(update={"strategy": seed})

    return node
