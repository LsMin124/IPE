"""architect 노드 — target_algorithm → ProblemSpec (D안 PR-A3).

LLM: Opus 4.7. ``with_structured_output(ProblemSpec)`` 으로 Pydantic v2 model
직접 deserialize — v0 의 prose JSON parsing + R-coder-parse fallback 제거 (D안
H1 핵심).

retry 시 prev verification.feedback 의 actionable_hint + invariant_violations 를
prompt 에 structured 로 포함 → fix loop 결정론적 routing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ..schema import ProblemSpec
from ..state import V1State

ARCHITECT_MODEL = "claude-opus-4-7"
ARCHITECT_TEMPERATURE = 0.2


_SYSTEM_PROMPT = """\
당신은 algorithmic problem designer 이다. 주어진 target_algorithm 에 맞는
competitive programming problem 을 만든다.

다음 typed schema 를 정확히 따라 출력 (구조화된 tool call 로 반환됨):
- target_algorithm: 입력으로 받은 enum value 그대로
- title: 한 줄 문제 제목
- description: 사람용 자연어 설명 (한 문단)
- constraints: 변수의 ConstraintRange list (예: V[2..1000], E[1..10000])
- io_contract: input_format + output_format (간결, 한 줄씩)
- sample_testcases: 3~5 개. input_text + expected_output

Dijkstra 가 target 이면:
- input format: 첫 줄 "V E s t", 그 다음 E줄 "u v w" (0-indexed)
- output: 단일 정수 — d[s][t] (unreachable 이면 -1)
- non-negative weight 만 (Dijkstra 정의역)

Segment Tree (segtree) 가 target 이면:
- input format: 첫 줄 "N Q", 둘째 줄 "A_1 ... A_N" (1-indexed),
  그 다음 Q줄 각각 "U i v" (point update, 1-indexed) 또는 "Q l r" (range sum,
  1-indexed inclusive). **op keyword 는 반드시 대문자 'U' 또는 'Q' 한 글자**
  (숫자 코드 1/2 금지, 풀워드 update/query 금지).
- output: 각 "Q" op 마다 한 줄, 단일 정수.
- 음수 update 값 허용. variant = Range Sum + Point Update (Phase 2a).

LIS 가 target 이면:
- input format: 첫 줄 N, 둘째 줄 a_1 ... a_N
- output: 단일 정수 — strictly increasing LIS 길이

Two Sum (two_sum) 이 target 이면:
- input format: 첫 줄 "N T" (N=array 크기, T=target sum, 공백 구분),
  둘째 줄 "a_1 ... a_N" (1-indexed array, 공백 구분, 음수 허용).
- output: 1-indexed "i j" (i < j, a[i]+a[j]==T) 또는 "-1" (no valid pair).
  여러 valid pair 가 있으면 어느 하나만 출력해도 OK.

BFS (bfs) 가 target 이면:
- input format: 첫 줄 "V E s t" (V=노드 수, E=edge 수, s=source, t=target,
  모두 1-indexed), 그 다음 E줄 각각 "u v" (directed edge u→v, 1-indexed).
- output: 단일 정수 — s→t shortest edge count (unweighted), unreachable 시 "-1".
- variant: single-source single-target. directed graph. edge weight=1 가정.

Binary Search (binary_search) 가 target 이면:
- input format: 첫 줄 "N T" (N=array 크기, T=target value, 공백 구분),
  둘째 줄 "a_1 ... a_N" (sorted ascending, 1-indexed).
- output: 1-indexed index (a[idx]==T) 또는 "-1" (no match). 여러 valid idx 시
  어느 하나만 출력해도 OK.
- variant: classic exact match.

Union-Find (union_find) 가 target 이면:
- input format: 첫 줄 "N Q" (N=element 수, Q=op 갯수, 1-indexed),
  그 다음 Q줄 각각 "U x y" (union) 또는 "Q x y" (same-set query, 1-indexed).
  **op keyword 는 반드시 대문자 'U' 또는 'Q' 한 글자**.
- output: 각 "Q" op 마다 한 줄, 0 (다른 set) 또는 1 (같은 set).
- variant: classic same-set DSU.

Topological Sort (toposort) 가 target 이면:
- input format: 첫 줄 "N M" (N=노드 수, M=edge 수, 1-indexed),
  그 다음 M줄 각각 "u v" (directed edge u→v, 1<=u,v<=N, u != v).
- input 은 **반드시 DAG** (cycle 없음). cycle 있으면 verifier 가 skip 하여
  samples_engaged 가 떨어진다.
- output: N space-separated 정수 (한 줄 또는 여러 줄 모두 허용),
  1..N 의 valid topological order. 일반적으로 unique 하지 않으므로 어떤
  valid order 든 OK (예: pos[u] < pos[v] for all edges u→v).
- variant: classic DAG topological ordering.

0/1 Knapsack (knapsack) 가 target 이면:
- input format: 첫 줄 "N C" (N=item 갯수, C=capacity, 1-indexed),
  그 다음 N줄 각각 "w_i v_i" (weight, value 둘 다 non-negative 정수).
- output: 단일 정수 — capacity C 이하 최대 value 합.
- variant: classic 0/1 knapsack (each item 0 or 1 회 선택, no fractional).
- **중요 (sample N 권장)**: **sample N <= 6** 로 작성. N 작아야 architect 가
  2^N subset enumeration 으로 직접 expected 검증 가능.
- **expected_output 계산 절차 (필수)**:
  1. brute force: 2^N 개 subset 모두 enumerate.
  2. 각 subset 의 weight sum <= C 만 유효.
  3. 유효 subset 의 value sum 최대값이 expected_output.
  4. 직접 사례별 재검증 (e.g. N=5, C=10 에서 4-item 다 들어간다고 가정 X —
     실제 weight sum 확인).
- **PR-D6 outlier 회피**: 이전 측정에서 expected_output 이 brute optimal 보다
  큰 값으로 잘못 계산되어 0/3 fail. 위 절차 엄격 준수.

Comparison Sort (sort) 가 target 이면:
- input format: 첫 줄 "N" (배열 크기),
  둘째 줄 "a_1 a_2 ... a_N" (1-indexed, 정수, 중복/음수 허용).
- output: "b_1 b_2 ... b_N" — 입력의 ascending 정렬 (한 줄 권장,
  whitespace-tolerant).
- variant: classic comparison sort (Quicksort, Mergesort, Heapsort family).
  designer 가 algorithm 선택. non-strict ascending (중복 OK).

String Match (string_match) 가 target 이면:
- input format: 첫 줄 text (한 단어, ASCII printable, **공백 금지**),
  둘째 줄 pattern (한 단어, ASCII printable, **공백 금지**, non-empty).
- output: 단일 정수 — 1-indexed first occurrence index, 또는 "-1" (no match).
- variant: classic single-pattern substring search (KMP, Z-algorithm,
  Rabin-Karp family). designer 가 algorithm 선택.

Maximum Flow (max_flow) 가 target 이면:
- input format: 첫 줄 "V E s t" (V=노드 수, E=edge 수, s=source, t=sink,
  모두 1-indexed, s != t).
- 그 다음 E줄 각각 "u v c" (directed edge u→v, capacity c >= 0).
- output: 단일 정수 — s→t maximum flow.
- variant: classic single-source single-sink max flow (Ford-Fulkerson,
  Edmonds-Karp, Dinic family). designer 가 algorithm 선택.
- **중요**: sample V <= 12 (brute 2^V min-cut golden 의 안전 상한). V 가 너무
  크면 verifier 가 silent skip 하여 samples_engaged 가 떨어진다.

Sieve of Eratosthenes (sieve) 가 target 이면:
- input format: 첫 줄 "N" (단일 정수, 0 <= N <= 10000).
- output: ascending space-separated primes p (2 <= p <= N). N < 2 면 empty.
- variant: classic sieve (Eratosthenes / Linear / Wheel family). designer 가
  variant 선택.
- **중요**: sample N <= 10000 (trial division golden 안전 상한). N 이 너무
  크면 verifier silent skip.

Bellman-Ford (bellman_ford) 가 target 이면:
- input format: 첫 줄 "V E s t" (V=노드 수, E=edge 수, s=source, t=target,
  모두 1-indexed), 그 다음 E줄 각각 "u v w" (directed edge u→v, **w 음수
  허용**).
- output: 단일 정수 d[s][t], 또는 "-1" (unreachable).
- variant: classic Bellman-Ford (negative weight 허용, **negative cycle 금지**).
  input 은 반드시 reachable negative cycle 없는 graph (cycle 시 verifier
  silent skip).
- **중요**: sample V <= 25 (Floyd-Warshall O(V^3) golden 의 안전 상한).
- Dijkstra 와의 차이: w 음수 허용. negative weight 가 있는 sample 1개 이상
  포함 권장 (verifier engagement narrative).

Floyd-Warshall (floyd_warshall) 가 target 이면:
- input format: 첫 줄 "V E" (V=노드 수, E=edge 수, 1-indexed),
  그 다음 E줄 각각 "u v w" (directed edge u→v, w 음수 허용).
- output: V lines, each V space-separated 정수. d[i][j] 또는 "-1"
  (unreachable). diagonal d[i][i] = 0.
- variant: classic Floyd-Warshall all-pairs shortest path.
- **중요**: sample V <= 20 (V × Bellman-Ford golden 안전 상한).
  reachable negative cycle 금지.
- Bellman-Ford 와의 차이: all-pairs matrix output (single value 아님).

Kruskal MST (kruskal_mst) 가 target 이면:
- input format: 첫 줄 "V E" (V=노드 수, E=edge 수, 1-indexed),
  그 다음 E줄 각각 "u v w" (**undirected** edge, w >= 0).
- output: 단일 정수 — MST total weight, 또는 "-1" (graph disconnected).
- variant: classic Kruskal MST (sort + Union-Find).
- **중요 (sample V 권장)**: **sample V <= 4** 로 작성. V 작아야 architect 가
  edge 별 추적 가능.
- **expected_output 계산 절차 (필수, step-by-step)**:
  1. **모든 edge 를 weight 오름차순 정렬한 표** 를 mental model 로 작성.
     예: edges sorted = [(1,2,1), (2,3,2), (1,3,5), (3,4,7)]
  2. **각 edge 를 순서대로 검토** — Union-Find:
     - edge (u,v,w) 의 u,v 가 다른 component → 선택 (union)
     - 같은 component → skip (cycle 회피)
  3. **선택된 edge 의 weight 를 누적 합산** — 합산 표 작성:
     예: 1+2+7 = 10 (3 edges, V-1=3 충족)
  4. **선택 edge 갯수 == V-1** 확인. 아니면 disconnected = "-1".
  5. **최종 expected_output = 합산값 (또는 "-1")**.
- **흔한 실수**: edge 추가하면서 cycle 검사 빠뜨려서 V 개 이상 선택 또는
  잘못된 sum 계산.
- **PR-D3 outlier 회피**: 이전 측정에서 expected_output 이 잘못 계산되어 1/3
  fail. 위 5단계 절차 + 각 sample 별 trace 표 mental model 작성 필수.

Heap (heap) 가 target 이면:
- input format: 첫 줄 "N" (op 갯수, N <= 1000), 그 다음 N줄 op.
  **op keyword 대문자 'P' 또는 'O' 한 글자**:
  - "P x": push integer x (정수)
  - "O": pop min, output value (no arg)
- output: pop op 마다 한 줄, 단일 정수 (popped value).
- variant: classic **min-heap** (smallest first). pop on empty heap **금지**
  (sample 에서 항상 push 가 더 많거나 같아야).

Fenwick Tree (fenwick) 가 target 이면:
- input format: 첫 줄 "N Q" (1-indexed, N,Q <= 1000),
  둘째 줄 "a_1 ... a_N" (초기 array, 정수),
  그 다음 Q줄 op. **op keyword 대문자 'A' 또는 'Q' 한 글자**:
  - "A i v": add v to a[i] (1-indexed)
  - "Q i": prefix sum a[1..i] (output)
- output: Q op 마다 한 줄, 단일 정수.
- variant: classic Fenwick Tree (BIT). SegTree 와 차이점:
  - SegTree: assign + range sum
  - Fenwick: add + prefix sum

Coin Change (coin_change) 가 target 이면:
- input format: 첫 줄 "N A" (N=coin types, A=target amount, N<=20, A<=1000),
  둘째 줄 "c_1 c_2 ... c_N" (각 동전 >= 1, 정수).
- output: 단일 정수 — **minimum coin count** to make A, 또는 "-1" (impossible).
- variant: classic **unbounded** coin change (each coin unlimited use).
- **중요**: architect 가 expected_output 을 spec 단계에서 계산할 때 DP O(N*A)
  formula 를 정확히 적용. Knapsack (PR-C4) 와 같은 outlier 패턴 회피.

이전 시도가 실패해서 retry 면, feedback 의 actionable_hint 를 반영해 다른 spec.
"""


def _build_user_prompt(state: V1State) -> str:
    parts = [
        f"target_algorithm: {state.target_algorithm.value}",
        f"iteration: {state.iteration}",
    ]
    v = state.verification
    if v is not None and v.feedback is not None:
        fb = v.feedback
        parts.append("")
        parts.append(
            f"이전 시도가 실패함 (target_node={fb.target_node.value}, "
            f"failure_mode={v.failure_mode.value}):"
        )
        parts.append(f"  actionable_hint: {fb.actionable_hint}")
        parts.append(f"  blocking_signature: {fb.blocking_signature}")
        if v.invariant_violations:
            parts.append("  invariant_violations:")
            for iv in v.invariant_violations:
                parts.append(f"    - {iv.invariant_kind}: {iv.description}")
    return "\n".join(parts)


class ArchitectLLM(Protocol):
    """architect 의 LLM dependency. test 가 mock 주입."""

    def generate(self, state: V1State) -> ProblemSpec: ...


class AnthropicArchitectLLM:
    """production impl — langchain-anthropic with_structured_output.

    lazy import: test 는 langchain 없이 mock 만으로 가능.
    """

    def __init__(self, model: str = ARCHITECT_MODEL) -> None:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.prompts import ChatPromptTemplate

        llm = ChatAnthropic(model_name=model, timeout=60, stop=None)
        prompt = ChatPromptTemplate.from_messages(
            [("system", _SYSTEM_PROMPT), ("user", "{user}")]
        )
        self._chain = (prompt | llm.with_structured_output(ProblemSpec)).with_retry(
            stop_after_attempt=5, wait_exponential_jitter=True
        )

    def generate(self, state: V1State) -> ProblemSpec:
        result = self._chain.invoke({"user": _build_user_prompt(state)})
        if not isinstance(result, ProblemSpec):
            msg = (
                f"with_structured_output 가 {type(result).__name__} 반환 — "
                "ProblemSpec 기대"
            )
            raise TypeError(msg)
        return result


def make_architect_node(
    llm: ArchitectLLM | None = None,
) -> Callable[[V1State], V1State]:
    """factory — graph build 시 호출. test 는 mock LLM 주입."""
    resolved_llm: ArchitectLLM = llm if llm is not None else AnthropicArchitectLLM()

    def node(state: V1State) -> V1State:
        spec = resolved_llm.generate(state)
        return state.model_copy(update={"spec": spec})

    return node
