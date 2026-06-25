"""v2-native reconciler 노드 — sample + 퇴화 엣지 입력으로 differential 확장 (Phase 5a).

v1 ``make_reconciler_node`` 와 동일하게 순수 ``reconcile()`` 를 호출하지만, differential
입력 집합을 ``sample_testcases`` 에 더해 backbone 이 IR 에서 파생한 **퇴화 엣지 입력**까지
확장한다 (RFC §6 Tier B 유일성). 독립 골든들이 퇴화 입력에 합의하면 그 엣지는 well-posed·
출력이 operationally 정의됨; 불합의면 그 입력이 witness 인 ill-posed IR → reconcile reject
(``disagreements`` 에 그 입력이 증거로 남는다).

엣지 입력은 ``resolve_backbone(io_schema).derive_edge_inputs`` 에서 — graph(graph_shape 핀)·
sequence(sequence_shape 핀) 백본이 파생하고, 어느 백본도 소유 안 한 타입은 NullBackbone=빈
튜플. 백본을 추가할수록 확장 대상이 자연히 넓어진다(seam — skeleton 무수정). 같은 퇴화 입력을
``resolved_edges``(pending)로 기록해 하류 edge_filler 가
canonical golden 으로 expected 를 채운다 — diff 한 입력 == fill 하는 입력(동일 파생, 단일 기록).

v1 reconciler 를 대체하는 v2 노드인 이유: 엣지 파생이 ``state.blueprint``(V2 전용) + backbone
seam 을 읽어야 하는데, v1 노드는 V1State·해자 코드라 v2 의존을 두지 않는다. 순수 ``reconcile()``
는 그대로 재사용한다(해자 differential). LLM 없음 — runner 주입(단위 테스트는 scripted mock).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ipe.v1.schema import ResolvedEdgeCase
from ipe.v1.verification._exec import (
    DEFAULT_MEMORY_LIMIT_MB,
    DEFAULT_TIME_LIMIT_MS,
    CodeRunner,
)
from ipe.v1.verification.reconcile import reconcile

from ..backbone import DegenerateInput, resolve_backbone
from ..state import V2State


def make_v2_reconciler_node(
    runner: CodeRunner,
    *,
    time_limit_ms: int = DEFAULT_TIME_LIMIT_MS,
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
) -> Callable[[V2State], dict[str, Any]]:
    """fan-in reconciler 노드 — ``candidates`` 를 sample+엣지 입력으로 reconcile.

    입력 샘플은 ``state.spec`` 에서, 퇴화 엣지 입력은 ``state.blueprint`` 의 io_schema 에서
    backbone 이 파생한다. spec 부재면 차분 불가 → 예외(v1 reconciler 와 동일 계약).
    반환은 partial dict ``{reconciliation, resolved_edges}`` — ``dict[str, Any]`` 주석으로
    langgraph 의 typed-channel 추론(candidates reducer 충돌)을 막는다.
    """

    def node(state: V2State) -> dict[str, Any]:
        if state.spec is None:
            msg = "reconciler requires state.spec (differential 입력 추출용)"
            raise ValueError(msg)
        sample_inputs = [s.input_text for s in state.spec.sample_testcases]
        edges: tuple[DegenerateInput, ...] = ()
        if state.blueprint is not None:
            io_schema = state.blueprint.io_schema
            edges = resolve_backbone(io_schema).derive_edge_inputs(io_schema)
        inputs = [*sample_inputs, *(e.input_text for e in edges)]
        result = reconcile(
            candidates=state.candidates,
            inputs=inputs,
            runner=runner,
            time_limit_ms=time_limit_ms,
            memory_limit_mb=memory_limit_mb,
        )
        resolved = tuple(
            ResolvedEdgeCase(
                name=e.name, input_text=e.input_text, rationale=e.rationale
            )
            for e in edges
        )
        return {"reconciliation": result, "resolved_edges": resolved}

    return node
