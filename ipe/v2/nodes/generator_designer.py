"""generator_designer 노드 — frozen io_schema → GeneratorContract (순수 투영, Phase 3).

이전엔 Opus LLM 이 scale_families/edge_cases 를 저작했으나, 그 계약은 io_schema 가 이미
결정하는 정보(규모 tier·실현가능 퇴화)를 추가하지 않고 LLM 의 자유도는
``input_gen._edge_bias`` 가 5개 bias 로 접었다 — unrealizable 카테고리(self_loop·특정
위상 등)를 채점셋 이름에 남겨 형식 계약과 모순시키는 리스크(F18 reject, N=18 실측)만
더했다. 단일 IR 리팩터(RFC §4)는 이 노드를 io_schema 의 **순수 투영**으로 강등한다:
``derive_generator_contract`` 가 sized 필드 size_range 를 log-spaced scale tier 로,
실현가능 퇴화를 edge case 로 결정론 파생한다. 효과 = Opus 호출 1 삭제 + unrealizable
fail_qa → 0. 계약이 io_schema 의 함수이므로 다른 투영과 모순 불가(consistency-by-construction).

suite 구성 계획 (상용 저지 케이스 강도): boundary/퇴화(min_size/max_size/empty/
disconnected — 기존 유지) 위에 adversarial 아키타입을 각 1개 이상 얹는다 — graph:
path_chain/star/dense/cycle_heavy/duplicate_edges/equal_weights/extreme_weights,
sequence: sorted_asc/sorted_desc/all_equal/alternating/single_element/extreme_values,
string: all_same_char/periodic. ``max_stress`` 가 max_size 와 별도로 상한을 한 번 더
때려 진짜 상한(naive TLE 유도) 케이스를 최소 2개 보장한다. 전부 shape 핀
(sortedness/duplicates/multi_edges/connectivity) 존중 + 규모 게이트
(``input_gen._ADVERSARIAL_MIN_SIZE`` — 소규모 스키마는 기존 집합 그대로, 대형 케이스
추가 비용은 max_stress 1개뿐). 파생 로직은 ``input_gen._derive_adversarial_edges``
단일 소스 (이 노드는 여전히 순수 위임 — F18 카테고리명↔입력 정합 불변).
"""

from __future__ import annotations

from collections.abc import Callable

from ..generation.input_gen import derive_generator_contract
from ..state import V2State


def make_generator_designer_node() -> Callable[[V2State], V2State]:
    """factory — frozen blueprint.io_schema → GeneratorContract (순수, LLM 없음).

    formalizer 가 freeze 한 io_schema 만 보고 결정론 투영한다(carry-over 강제 불요 —
    계약은 io_schema 의 함수). validator(Phase 2)가 이미 io_schema 완전성(collection
    size_range·참조 해소)을 보장하므로 verification 통과 후 도달하는 io_schema 는 well-formed.
    """

    def node(state: V2State) -> V2State:
        bp = state.blueprint
        if bp is None:
            msg = "generator_designer requires state.blueprint — formalizer must run first"
            raise ValueError(msg)
        contract = derive_generator_contract(bp.io_schema)
        return state.model_copy(update={"generator_contract": contract})

    return node
