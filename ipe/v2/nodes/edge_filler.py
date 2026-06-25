"""edge_filler 노드 (v2, Phase 5a) — canonical golden 실행으로 resolved_edges expected 채움.

sample_filler 와 동일 원칙(정답은 golden 부트스트랩, RFC §3.3): reconcile 채택 canonical
golden 을 각 퇴화 엣지 입력에 실행해 stdout 을 ``ResolvedEdgeCase.expected_output`` 으로
채운다. 이로써 엣지 의미가 formalizer prose 처방이 아니라 **golden 으로 operationally
정의**된다 — IR 은 어떤 퇴화가 있는지(파생)만 선언하고, golden 이 무엇을 하는지(reconcile
로 유일성 검증된 출력)를 정의한다.

reconcile(v2)이 이미 같은 퇴화 입력으로 골든들 합의(differential)를 통과시킨 뒤라 이 시점엔
canonical 이 그 입력들을 성공 실행한다. 방어적으로 실행 실패한 엣지는 expected=None(pending)
그대로 둔다(drop 아님 — 진단 보존). ``resolved_edges`` 가 비면(스칼라 only 등) no-op.
reconcile 채택 경로 뒤에만 배선되므로 ``canonical_code`` 는 존재한다(방어적 None 가드).

반환은 sample_filler(twin filler)와 동일하게 full ``V2State`` — partial dict 가 아니다. v2
graph 의 ``candidates``/``qa_reviews`` reducer(``_merge_candidates``)는 **동일 재emit 에 멱등**
(frozen 후보 값동등으로 ``c not in merged`` 가 정확, M2 step4 발견)이라 full-state 재emit 이
후보를 더블 누적하지 않는다 — sample_filler·executor 와 같은 패턴(twin 일관). reconciler 가
``dict[str, Any]`` 를 쓰는 건 별개 이유(fan-in 의 typed-channel 스키마 추론 회피).

LLM 없음 — runner 주입(``run_code`` 재사용). 테스트는 mock runner 로 sandbox 없이 검증.
"""

from __future__ import annotations

from collections.abc import Callable

from ipe.v1.schema import ResolvedEdgeCase
from ipe.v1.verification._exec import (
    DEFAULT_MEMORY_LIMIT_MB,
    DEFAULT_TIME_LIMIT_MS,
    CodeRunner,
    run_code,
)

from ..state import V2State


def make_edge_filler_node(*, runner: CodeRunner) -> Callable[[V2State], V2State]:
    """factory — reconcile canonical golden 으로 resolved_edges expected 채움. LLM 없음.

    실행 OK 인 엣지만 expected 를 채우고, 실패분은 pending(None) 유지(진단 보존).
    reject 경로/resolved_edges 빈(스칼라 only) 면 방어적 no-op.
    """

    def node(state: V2State) -> V2State:
        rec = state.reconciliation
        if rec is None or rec.canonical_code is None or not state.resolved_edges:
            return state  # reject 경로/resolved_edges 빈 — 방어적 no-op
        golden = rec.canonical_code
        filled: list[ResolvedEdgeCase] = []
        for edge in state.resolved_edges:
            result = run_code(
                runner,
                golden,
                edge.input_text,
                DEFAULT_TIME_LIMIT_MS,
                DEFAULT_MEMORY_LIMIT_MB,
            )
            if result.status == "OK":
                filled.append(
                    edge.model_copy(
                        update={"expected_output": result.stdout.strip()}
                    )
                )
            else:
                # golden 실행 실패 — pending 유지(엣지 의미 미정의로 남김, drop 안 함).
                filled.append(edge)
        return state.model_copy(update={"resolved_edges": tuple(filled)})

    return node
