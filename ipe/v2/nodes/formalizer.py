"""Formalizer 노드 — StrategySeed → ProblemBlueprint FREEZE (M3 step2, blueprint-first).

LLM: Opus 4.7 (정밀 형식 동결). 책임 = frozen strategy 위에 io_schema +
output_invariants 를 붙여 ``ProblemBlueprint`` 로 **freeze**. 알고리즘 결정
(reduction_core/composition/domain)은 Strategist 가 정한 것을 노드가 **구조적으로
carry-over** — Formalizer LLM 은 ``BlueprintFormalization``(형식 면만) 만 산출하므로
은닉 코어를 임의로 바꿀 수 없다 (freeze 규율, 상관오독 §7 방어).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ipe.v1.schema import BlueprintFormalization, ProblemBlueprint, is_basic

from ..config import ABSTRACT_DOMAIN
from ..state import V2State

FORMALIZER_MODEL = "claude-opus-4-8"
FORMALIZER_TEMPERATURE = 0.2  # 정밀 동결 (발산 금지, Strategist 의 0.7 와 대비)


_SYSTEM_PROMPT = """\
당신은 algorithmic problem formalizer 다. 이미 정해진 전략 시드(숨은 알고리즘 +
합성 기법 + 위장 도메인)를 받아, 그 문제의 **형식 계약**을 정밀하게 동결한다.

typed BlueprintFormalization (구조화된 tool call) 로 반환 — 형식 면만:
- io_schema:
  - inputs: 입력 각 필드의 IOFieldSpec (name + type + size_range/value_range).
    type 은 int / int_array / int_matrix / float / string / bool / weighted_edges /
    tree_edges / grid 중. 크기·값 범위를 ConstraintRange 로 명시.
  - output_type: int / int_array / float / bool / string / yes_no 중.
  - output_format: 출력 인쇄 형식 (한 줄 설명).
- output_invariants: 출력이 **항상** 만족하는 관계 (symbolic invariant 후보). 각
  kind + description. 예: non_negative / monotonic / sum_conserved / bounded_by_input.

규율:
- 알고리즘/도메인을 **재결정하지 말 것** — 시드의 reduction_core/composition/domain
  은 이미 동결되어 그대로 유지된다. 당신은 입출력 형식과 불변식만 형식화한다.
- io_schema 는 정답을 노출하지 않는 **중립 형식 계약** 이어야 한다 (은닉 유지).
- output_invariants 는 정답 검증에 쓰일 만큼 구체적이되 풀이를 누설하지 않게.
- graph 입력은 **self-contained 단일 필드** 로 모델링한다 (weighted_edges/tree_edges
  는 V·E 헤더를 자체 포함 — 정점 수 V 를 별도 int 필드로 분리하지 말 것: 입력 생성기
  와 중복/모순을 만든다). 정점 수 범위는 그 graph 필드의 size_range 로 표현한다.
- 정점/원소/행을 가리키는 **스칼라 int 참조 필드**(출발/도착 s, t, 질의 인덱스 등)는
  value_range 를 **직접 잡지 말고** ``references`` 에 가리키는 collection 필드 이름을
  지정한다 (예: s.type=int, s.references="grid"). 입력 생성기가 그 필드의 **실제 생성
  크기**에 맞춰 ``[1, 실제크기]`` 1-indexed 로 생성한다. value_range 로는 데이터 의존
  차원(V 는 1~수십만 가변)을 표현할 수 없다 — 정적 ``[1,2]`` 로 잡으면 질의가 1·2 뿐인
  **trivial 퇴화**(QA difficulty reject), ``[1,V상한]`` 으로 잡으면 작은 그래프에서
  **V 초과 범위밖 입력**(정해 IndexError → fail_synthesis)이 된다. ``references`` 가
  둘 다 구조적으로 차단한다 (정점 질의는 거의 항상 이 방식).
- **중복 카운트 금지** (위 graph 규율을 모든 collection 으로 일반화): collection
  필드(int_array/int_matrix/grid/weighted_edges/tree_edges)는 canonical 직렬화에
  **자기 크기 헤더**(원소 개수 N / 행·열 R C / 정점·간선 V E)를 **자기접두**로 자체
  포함한다. 그 크기를 나타내는 **별도 스칼라 int 필드를 추가하지 말 것** — 예:
  int_array 앞에 원소 개수 N 을 따로 두면 생성 입력에 **개수가 두 번** 나타나(스칼라
  N, 그리고 배열 자체 헤더 → `5\\n5\\n3 1 4 1 5`) solver 가 입력 줄 수를 확정 못 하는
  모호 입력이 되어 QA ambiguity 게이트에서 reject 된다. collection 의 크기는 그
  필드의 size_range 로만 표현한다.
- int_matrix/grid 에서 각 레코드(행)가 **고정 개수 K개 속성**을 가지면 (예: '각 거래는
  [시각, 금액] 2개 값', '각 점은 [x, y, z] 좌표') ``cols_range=[K,K]`` 로 열 수를
  고정하고 ``size_range`` 는 행 수 N 으로 둔다. 고정하지 않으면 생성기가 열 수도
  무작위로 잡아 행마다 속성 수가 흔들리고, 정해가 ``row[2]`` 같은 고정 인덱스에서
  IndexError 로 깨진다(sort 계열 fail_synthesis 실측). 열 수가 진짜 가변이어야 하는
  문제(가변 길이 행)만 ``cols_range`` 를 비운다.
- io_schema 는 필드 집합이 **자기완결적 의미 정합**이어야 한다: 임계값/예산/필터
  같은 비교용 스칼라 필드는 그 **비교 대상**이 되는 per-element 데이터가 io_schema
  안에 실제로 존재할 때만 추가한다 (예: capacity_threshold 를 두려면 간선별 capacity
  가 입력에 있어야 한다 — 없으면 주어진 입력만으로 풀 수 없는 **고아 필드**가 된다).
  풀이에 역할이 없는 필드는 넣지 말 것.
- composition 이 비어있지 않으면 **합성이 필수**가 되도록 형식을 설계한다: 출력의
  의미가 reduction_core 의 정석 출력 그대로가 아니라, composition 기법을 거쳐야만
  계산되는 값이어야 한다 (예: binary_search 합성이면 reduction_core 를 feasibility
  판정으로 반복 호출해 찾는 최소/최대 임계값이 출력). 정석 출력에 장식만 더한 것은
  합성이 아니다 — 그런 문제는 고전 동형으로 QA 유출 게이트에서 reject 된다.
- graph 류 필드의 간선 속성은 **단일 가중치 w 하나뿐**이다 (weighted_edges 의
  canonical 직렬화 = 줄당 `u v w` — 간선당 추가 속성은 표현이 불가능하다). 간선
  **다속성**을 요구하는 설계(예: 손실+내경 두 값)는 금지. 필터/임계값류 합성은
  그 단일 w 에 대한 조건으로 설계한다 (예: 'w ≤ t 인 간선만 사용 가능할 때의
  최단 경로' — 임계값은 별도 스칼라 필드).
- **graph 구조 사실은 `graph_shape` 필드로 명시 결정한다** (weighted_edges/tree_edges):
  prose 가 아니라 IOFieldSpec.graph_shape 에 구조화해 emit 한다. 직렬화기가 이것을 단일
  진실로 READ 하고 narrative/QA/faithfulness 는 prose 규칙이 아니라 **기계 비교**로 검증한다.
  - **directed** (필수 결정): 간선이 단방향(u→v)인지 양방향(u↔v, 무방향)인지 **반드시
    정한다**. 오늘날 어디에도 결정 안 돼(F8) narrative 가 '양방향 도로'를 자유 서술하면
    golden·채점셋과 모순됐다. 도메인 의미로 판단한다 (도로/배관/통신망=양방향 흔함,
    의존성/작업흐름/일방통행=단방향).
  - **self_loops** (기본 false): 자기 간선(u==v) 허용 여부. 대개 false.
  - **multi_edges** (기본 true): 같은 쌍 중복 간선 허용. 단순 그래프만 false.
  - **connectivity** (기본 maybe_disconnected): "connected"=항상 연결 보장,
    "maybe_disconnected"=분리(도달 불가) 가능. tree_edges 는 트리 불변이므로
    connectivity="connected"·multi_edges=false·self_loops=false 로 emit 한다.
  퇴화/경계 출력 의미(아래 규율)는 graph_shape 가 **실제 허용하는** 구조에 대해서만
  정의한다 — graph_shape 가 부정하는 구조(self_loops=false 면 자기 간선)의 처리 의미는
  적지 말 것(입력에 없어 narrative·채점셋과 모순돼 QA reject, N=18 실측).
- **수열(int_array) 구조 사실은 `sequence_shape` 필드로 명시 결정한다** (graph_shape 의
  수열판): prose 가 아니라 IOFieldSpec.sequence_shape 에 구조화해 emit 한다. 직렬화기가
  이것을 단일 진실로 READ 해 실제 정렬/distinct 배열을 방출하고 narrative/QA/faithfulness
  는 기계 비교로 검증한다.
  - **sortedness** (필수 결정): 배열이 unsorted(무정렬)/non_decreasing(비내림차)/
    strictly_increasing(순증가, 중복 없음) 중 무엇인지 **반드시 정한다**. binary_search 는
    정렬 입력을 요구하는데 오늘날 어디에도 결정 안 돼, narrative 가 '정렬된 배열' 을 자유
    서술하면 직렬화기의 무정렬 배열·golden 과 모순됐다(graph 의 directed 와 같은 잠재 모순).
    알고리즘 의미로 판단한다 (이분탐색 대상 배열=정렬 필수, 그 외 대개 unsorted).
  - **duplicates_allowed** (기본 true): 같은 값 중복 허용. two_sum 의 서로 다른 원소
    같은 distinct 요구면 false. strictly_increasing 이면 자동 distinct 라 무의미.
  비-int_array 필드엔 sequence_shape 를 두지 말 것.
- **문자열(string) 구조 사실은 `string_shape` 필드로 명시 결정한다** (graph_shape 의
  문자열판): IOFieldSpec.string_shape 에 구조화해 emit 한다. 직렬화기가 이 문자 집합에서만
  문자를 뽑고 narrative/QA 가 기계 비교로 검증한다.
  - **alphabet** (필수 결정): 문자 집합을 lowercase(a-z)/uppercase(A-Z)/binary(01)/
    dna(ACGT)/alphanumeric(a-zA-Z0-9) 중 **반드시 정한다**. 오늘날 직렬화기는 a-z 만
    방출하는데 narrative 가 'DNA 서열'·'이진 문자열' 을 자유 서술하면 golden·채점셋과
    모순됐다. 도메인 의미로 판단한다 (유전체=dna, 비트열=binary, 일반 텍스트=lowercase).
  비-string 필드엔 string_shape 를 두지 말 것.
- io_schema 가 허용하는 **퇴화/경계 입력**의 출력 의미를 output_invariants 에
  명시적으로 **결정**해 둔다 (kind 예: edge_case_semantics): 시작==끝 같은 동일
  지점 케이스의 출력값, **도달 불가**·해 없음 케이스의 출력값(예: -1)과 그것이
  다른 실패 사유(예: 예산 초과)와 같은 값인지 구분되는지, 임계값·예산이 0 또는
  범위 하한일 때의 해석, **다중 간선**(같은 쌍에 여러 간선)·중복 값의 허용 여부와
  처리 방식. 이런 케이스의 출력이 미정의면 solver 가 해석을 강요받는 모호 문제가
  되어 QA ambiguity 게이트에서 reject 된다.
- **답 유일성(tie-break) 결정**: 출력이 선택·정렬·최적화를 거치는 문제는, 동률(tie —
  같은 정렬 키를 가진 원소, 복수의 최적해, 동일 max/min 값을 주는 서로 다른 후보)이
  존재할 때도 **답이 입력으로부터 유일하게 결정**되어야 한다. 두 방법 중 하나로
  보장한다: ① **출력을 tie-invariant 한 값으로 설계**(권장 — 길이/개수/합/최댓값처럼
  어느 최적해를 고르든 같은 값을 출력하면 동률이 답을 못 바꾼다) ② tie-invariant 가
  불가능하면 **동률 해소 규칙을 output_invariants 에 명시 결정**(kind 예:
  answer_uniqueness — 정렬 키 동률 시 2차/3차 기준, 동률 최적해 중 무엇을 답으로
  할지). 답이 유일하게 결정되지 않으면 solver 가 해석을 강요받아 QA ambiguity
  게이트에서 reject 된다.
"""


# 초급(easy track) formalizer system prompt — is_basic(seed) 일 때. 무거운 graph_shape/
# sequence_shape/edge_case_semantics/tie-break 머신을 **뺀** 단순 계약: 작은 입력·스칼라
# 또는 단순 배열·단순 출력·입력 의존. (알고리즘 경로의 그 머신이 binary_search/lis 의
# 'N=0↔constraints'·sortedness 모순을 만든 표면 — 초급엔 불필요하고 위험.)
_EASY_SYSTEM_PROMPT = """\
당신은 입문자용 코딩 문제 formalizer 다. 기초 카테고리(기본 입출력·산술/논리·조건 분기·
반복 누적)의 전략 시드를 받아, **간단하고 명확한** 입출력 형식 계약을 동결한다.

typed BlueprintFormalization (구조화된 tool call) 로 반환 — 형식 면만:
- io_schema:
  - inputs: 각 필드의 IOFieldSpec. type 은 대개 int 또는 int_array (필요시 string).
    크기·값 범위를 ConstraintRange 로 명시하되 **작게** 잡는다(입문 난이도): 스칼라 int 는
    보통 [1, 1000], 산술은 [-1000000000, 1000000000], 배열 size 는 [1, 100] 수준.
    거대한 N(수만~수십만)을 쓰지 말 것 — 입문은 작은 입력이다.
  - output_type: int / int_array / bool / string / yes_no 중 **가장 단순한** 것.
  - output_format: 출력 인쇄 형식 (한 줄 설명).
- output_invariants: 출력이 항상 만족하는 간단한 관계 0~1개면 충분(예: non_negative).

규율 (입문 평이함 — 알고리즘 구조 배제):
- 알고리즘/도메인을 재결정하지 말 것 — 시드의 reduction_core/domain 은 그대로 유지.
- **작고 단순하게**: 입력 필드 1~3개, 구조는 최소(스칼라 또는 단순 1차원 배열). graph/
  matrix/grid 같은 복잡 구조와 graph_shape/sequence_shape/string_shape 핀을 **쓰지 말 것**
  — 기초 입출력·산술·조건·반복은 그런 구조가 필요 없다.
- **출력은 반드시 입력에 의존**한다 — 입력과 무관한 상수 출력 금지(퇴화 → difficulty reject).
- 입력을 읽어 간단히 계산(합·차·비교·카운트·누적)해 출력하는 수준. 정렬·이분탐색·그래프
  같은 알고리즘을 끌어들이지 말 것(끌어들이면 입문이 아니다).
- 퇴화/경계 입력의 특별 처리(빈 입력·N=0 등)를 **지어내지 말 것** — constraints 가 허용하는
  범위만 다루면 충분하다(없는 케이스를 서술하면 constraints 와 모순돼 reject 된다).
- collection 필드(int_array)는 자기 크기 헤더를 자체 포함한다(별도 개수 스칼라 추가 금지)."""


def _build_user_prompt(state: V2State) -> str:
    strategy = state.strategy
    if strategy is None:
        msg = "formalizer requires state.strategy — strategist must run first"
        raise ValueError(msg)
    parts = [
        f"reduction_core (숨은 알고리즘): {strategy.reduction_core.value}",
        f"composition (합성 기법): {[a.value for a in strategy.composition]}",
        f"domain (위장 도메인): {strategy.domain}",
    ]
    if strategy.rationale:
        parts.append(f"rationale: {strategy.rationale}")
    if strategy.domain == ABSTRACT_DOMAIN:
        # 초급 abstract(orthogonal) — narrative 가 변수로 맨 서술하므로 io_schema 도
        # 추상 필드명·출력형식으로 정합시킨다(quantity↔N 같은 불일치=QA reject 방지).
        parts.append("")
        parts.append(
            "[추상 모드 — 도메인 스토리 없음]: io_schema 필드명을 추상 변수(A, B, N, P, "
            "arr 등)로 두고, output_format 도 도메인 용어 없이 수학적으로 서술하라 "
            "(quantity/price/balance 같은 도메인 명칭 금지). narrative 가 같은 변수로 서술한다."
        )
    # back-route 재진입 — 직전 IR 검증 위반을 수선 지시로 (첫 pass 는 validation=None
    # 이라 빈 추가 = 메인 경로 prompt 불변). narrative 의 QA 피드백 패턴과 동일.
    validation = state.validation
    if validation is not None and not validation.valid:
        parts.append("")
        parts.append(
            "[직전 IR 검증 실패 — 아래 위반을 해소하도록 io_schema 를 재설계하라]"
        )
        parts.extend(f"- {v}" for v in validation.violations)
    return "\n".join(parts)


class FormalizerLLM(Protocol):
    """Formalizer 의 LLM dependency. test 가 mock 주입."""

    def formalize(self, state: V2State) -> BlueprintFormalization: ...


class AnthropicFormalizerLLM:
    """production impl — Opus + structured output. lazy import (test 는 mock)."""

    def __init__(self, model: str = FORMALIZER_MODEL) -> None:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.prompts import ChatPromptTemplate

        llm = ChatAnthropic(model_name=model, timeout=60, stop=None)
        # 알고리즘(정밀 구조) 체인 — 비-basic seed. 기존과 동일(byte-identical).
        prompt = ChatPromptTemplate.from_messages(
            [("system", _SYSTEM_PROMPT), ("user", "{user}")]
        )
        self._chain = (
            prompt | llm.with_structured_output(BlueprintFormalization)
        ).with_retry(stop_after_attempt=5, wait_exponential_jitter=True)
        # 초급(단순) 체인 — is_basic seed. 무거운 구조 머신 배제(N=0/sortedness 모순 표면 제거).
        easy_prompt = ChatPromptTemplate.from_messages(
            [("system", _EASY_SYSTEM_PROMPT), ("user", "{user}")]
        )
        self._chain_easy = (
            easy_prompt | llm.with_structured_output(BlueprintFormalization)
        ).with_retry(stop_after_attempt=5, wait_exponential_jitter=True)

    def formalize(self, state: V2State) -> BlueprintFormalization:
        # 난이도는 seed 에서 파생(단일소스) — is_basic 이면 단순 형식 계약 경로.
        chain = self._chain_easy if is_basic(state.seed_algorithm) else self._chain
        result = chain.invoke({"user": _build_user_prompt(state)})
        if not isinstance(result, BlueprintFormalization):
            msg = (
                f"with_structured_output 가 {type(result).__name__} 반환 — "
                "BlueprintFormalization 기대"
            )
            raise TypeError(msg)
        return result


def make_formalizer_node(
    llm: FormalizerLLM | None = None,
) -> Callable[[V2State], V2State]:
    """factory — frozen strategy → ProblemBlueprint freeze. test 는 mock 주입.

    알고리즘 결정 필드(reduction_core/composition/domain)는 ``state.strategy`` 에서
    구조적으로 carry-over → Formalizer 가 은닉 코어를 못 바꾼다 (freeze 규율).
    """
    resolved_llm: FormalizerLLM = (
        llm if llm is not None else AnthropicFormalizerLLM()
    )

    def node(state: V2State) -> V2State:
        strategy = state.strategy
        if strategy is None:
            msg = "formalizer requires state.strategy — strategist must run first"
            raise ValueError(msg)
        formalization = resolved_llm.formalize(state)
        blueprint = ProblemBlueprint(
            reduction_core=strategy.reduction_core,
            composition=strategy.composition,
            domain=strategy.domain,
            io_schema=formalization.io_schema,
            output_invariants=formalization.output_invariants,
        )
        return state.model_copy(update={"blueprint": blueprint})

    return node
