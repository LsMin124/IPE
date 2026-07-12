"""spec_bridge 노드 — frozen blueprint + narrative + io_schema → ProblemSpec (순수 투영).

blueprint-first 모델링(strategy→blueprint→narrative)을 solver/executor 입력 계약
``ProblemSpec`` 으로 **순수 코드 투영**한다. 이로써 M2 full-mode synthesis(golden/brute
fan-out → differential reconcile → 검증)를 v2 에서 재사용할 토대가 된다.

**Phase 4 (RFC §F21) — Opus 호출 강등**: 이전엔 Opus 가 ``ProblemSpec`` 을 저작했으나
노드의 carry-over/투영 override 뒤 살아남는 저작 필드는 ``title`` 하나뿐이었다(나머지는
이미 코드가 강제). title 저작을 narrative(creative slot 1)로 접고 이 노드를 순수 투영으로
강등 — Opus 호출 1 삭제 + structured-output 실패 클래스(``fail_spec_authoring``) 제거.
계약이 IR(blueprint/narrative/io_schema)의 함수이므로 다른 투영(io_contract/parser/생성기)과
모순 불가(consistency-by-construction). generator_designer(Phase 3)와 동일한 강등.

**sample expected 는 golden 실행으로** (사용자 원칙, RFC §7 — 정답은 golden 부트스트랩):
node 가 sample **input 만** io_schema 에서 결정적 생성하고 expected 를 비운다. reconcile
뒤 ``sample_filler`` 노드가 canonical golden 실행으로 채운다. 이로써 LLM 직접 토큰
expected 생성 + ``sample_mismatch``(손계산 오답) 결함을 동시 제거. golden 정확성은
golden↔brute differential + symbolic 이 보장.

**투영 규율** (각 필드의 단일 저작 소스):
- ``target_algorithm`` = ``blueprint.reduction_core`` (verifier dispatch).
- ``title`` = ``narrative.title`` (narrative author 가 은닉/유출 규율 하에 저작).
- ``description`` = ``narrative.scenario`` (faithfulness 검증된 은닉 지문).
- ``constraints`` = ``render_constraints(io_schema)`` (V/N/R 크기·참조·고정 열·값 범위).
- ``io_contract`` = canonical 렌더 — ``input_format`` 은 ``render_input_format`` (입력
  생성기 직렬화와 동일 규약), ``output_format`` 은 ``io_schema.output_format`` carry-over.
  LLM prose 가 형식 계약을 정하면 생성 입력과 golden 파서가 어긋난다 — dijkstra anchor
  ratio 0.0 으로 실증된 불일치의 구조적 해소.
- ``input_parser_code`` = ``render_input_parser(io_schema)`` (생성기 직렬화의 역함수,
  round-trip 가드 — synthesis 코더가 파서 분산 없이 알고리즘만 작성).
- ``sample_testcases`` = io_schema 결정적 생성(형식 정합), expected 는 sample_filler 채움.
"""

from __future__ import annotations

from collections.abc import Callable

from ipe.v1.schema import (
    ConstraintRange,
    GeneratorContract,
    IOContract,
    IOSchema,
    OutputInvariant,
    ProblemSpec,
    SampleTestCase,
    ScaleFamily,
)

from ..generation.input_gen import (
    derive_degenerate_inputs,
    generate_inputs,
    render_constraints,
    render_input_format,
    seed_from_run_id,
)
from ..generation.input_parser import render_input_parser
from ..state import V2State

# 샘플 개수 + sized 필드(배열/그래프/행렬) 크기 상한 + 원소·스칼라 값 크기 상한.
# 샘플은 지문 예시 겸 reconcile 교차검증 입력 — 작고 형식-정합이어야 한다. 값까지 작게
# 잡는 이유(실측): size 만 줄이고 io_schema value_range(예: 20만)를 그대로 두면 골든이
# 큰 값으로 DP 배열 인덱싱해 IndexError(coin_change 실측) → 형식은 맞아도 fail_synthesis.
_SAMPLE_COUNT = 3
_SAMPLE_SIZE_MAX = 5
_SAMPLE_VALUE_MAX = 20

# 퇴화 의미(도달 불가→-1 등)를 결정해 둔 invariant kind 의 표식 — formalizer 가
# "edge_case_semantics" 류로 저작한다(LLM 저작 kind 라 접두 변형 허용, substring 매칭).
_EDGE_INVARIANT_MARKER = "edge_case"


def _has_edge_semantics(invariants: tuple[OutputInvariant, ...]) -> bool:
    """output_invariants 에 edge_case_semantics 류 invariant 가 있나 (순수 판정)."""
    return any(_EDGE_INVARIANT_MARKER in inv.kind for inv in invariants)


def _clamp_value_range(cr: ConstraintRange | None) -> ConstraintRange | None:
    """value_range 를 작은 창으로 clamp (부호 보존). 빈/단일값이면 그대로 유지."""
    if cr is None:
        return None
    lo = max(cr.min_value, -_SAMPLE_VALUE_MAX)
    hi = min(cr.max_value, _SAMPLE_VALUE_MAX)
    if lo > hi:  # 원 범위가 큰 양수/음수에 치우침 → 경계값 하나로 퇴화 (항상 유효)
        hi = lo
    return cr.model_copy(update={"min_value": lo, "max_value": hi})


def _sample_io_schema(io_schema: IOSchema) -> IOSchema:
    """샘플용으로 sized 필드 크기와 모든 값 범위를 작게 clamp 한 io_schema 복제.

    size 는 field_bounds 로(generate_inputs tier), 원소/스칼라 값은 io_schema 의
    value_range 가 직접 쓰이므로(_element_bounds 는 tier 무관) **schema 자체를 clamp**
    해야 작은 값이 나온다.
    """
    clamped_fields = tuple(
        f.model_copy(update={"value_range": _clamp_value_range(f.value_range)})
        for f in io_schema.inputs
    )
    return io_schema.model_copy(update={"inputs": clamped_fields})


def _generate_sample_inputs(
    io_schema: IOSchema,
    run_id: str,
    *,
    output_invariants: tuple[OutputInvariant, ...] = (),
) -> list[str]:
    """io_schema 에서 결정적·형식정합·소규모 샘플 입력 생성 (A: 샘플-too-short 해소).

    composition 으로 io_schema 필드가 늘면 LLM 저작 input 은 토큰이 모자라 골든 파서가
    IndexError → reconcile 이 'golden 이 샘플도 못 푼다'고 거부(fail_synthesis)하던 것을,
    input_gen 직렬화(=골든이 받는 파서 #2 의 짝)로 만들어 필드 수·헤더가 항상 일치하게
    한다. 크기·값 모두 작게 clamp(가독성+큰 값 골든 IndexError 방지). expected 는 하류
    sample_filler 가 golden 실행으로 채운다.

    **경계 샘플 1개 보장** (BOJ 표준 — 지문의 퇴화 의미를 샘플이 시연):
    ``output_invariants`` 에 edge_case_semantics 류 invariant 가 있고 backbone 파생
    퇴화 입력(``derive_degenerate_inputs``)이 비어있지 않으면 **마지막 샘플 1개**를 그
    첫 퇴화 입력으로 교체한다(개수 불변). '도달 불가 → -1' 같은 지문 처방이 샘플로
    실증돼 solver 가 퇴화 해석을 추측하지 않는다. 순수성(LLM 0) 유지 — 둘 다 IR 의
    결정론 함수.
    """
    schema = _sample_io_schema(io_schema)
    bounds = tuple(
        ConstraintRange(
            name=f.name,
            # size_range.min 존중(상한 sample max 로 클램프) — min=1 강제 시 V≥2 스키마에
            # V=1 샘플('1 0')이 생성돼 코드파생 constraints(V≥2)와 모순돼 QA reject(실측).
            min_value=min(f.size_range.min_value, _SAMPLE_SIZE_MAX),
            max_value=_SAMPLE_SIZE_MAX,
        )
        for f in schema.inputs
        if f.size_range is not None
    )
    contract = GeneratorContract(
        scale_families=(
            ScaleFamily(name="sample", case_count=_SAMPLE_COUNT, field_bounds=bounds),
        )
    )
    # distinct_index_refs — 샘플은 s==t 충돌을 회피해 핵심 로직(경로 탐색 등)을 실제로
    # 시연한다 (전 샘플 s==t 로 '항상 0 출력' trivial 해석이 가능하던 QA blocker,
    # run v2-54d68df4 실측). 퇴화 의미 시연은 아래 degenerate 교체 샘플이 전담.
    cases = generate_inputs(
        contract, schema, seed=seed_from_run_id(run_id), distinct_index_refs=True
    )
    texts = [c.input_text for c in cases]
    if _has_edge_semantics(output_invariants):
        # 원본 io_schema 로 파생 — 퇴화 입력은 이미 최소 규모(min/unreachable)라 clamp
        # 불필요 + reconcile/edge_filler 가 쓰는 것과 동일 입력(고정 seed, IR 함수).
        degenerate = derive_degenerate_inputs(io_schema)
        if degenerate:
            texts[-1] = degenerate[0][1]  # (name, input_text, rationale) 의 input_text
    return texts


def make_spec_bridge_node() -> Callable[[V2State], V2State]:
    """factory — blueprint+narrative+io_schema → ProblemSpec (순수 투영, LLM 없음).

    모든 필드가 IR 의 함수 — carry-over override 가 아니라 처음부터 투영으로 구성한다
    (Phase 4, RFC §F21). blueprint/narrative 부재는 배선 버그이므로 즉시 raise
    (오류 은폐 방지). validator(Phase 2)가 이미 io_schema well-formedness 를 보장.
    """

    def node(state: V2State) -> V2State:
        bp = state.blueprint
        nar = state.narrative
        if bp is None or nar is None:
            msg = "spec_bridge requires state.blueprint and state.narrative"
            raise ValueError(msg)
        spec = ProblemSpec(
            # target_algorithm 은 blueprint.reduction_core (verifier dispatch).
            target_algorithm=bp.reduction_core,
            # title 은 narrative author 저작 (creative slot 1, 은닉/유출 규율 하).
            title=nar.title,
            # description 은 faithfulness 검증된 은닉 지문.
            description=nar.scenario,
            # constraints/io_contract/parser 는 io_schema 코드 투영 — 입력 생성기·골든
            # 파서와 같은 단일 규약(graph V 를 E 로 오라벨·참조 리터럴 모순을 구조 차단).
            constraints=render_constraints(bp.io_schema),
            io_contract=IOContract(
                input_format=render_input_format(bp.io_schema),
                output_format=bp.io_schema.output_format,
            ),
            input_parser_code=render_input_parser(bp.io_schema),
            # sample input 은 io_schema 결정적 생성(형식 항상 정합), expected 는 하류
            # sample_filler 가 canonical golden 실행으로 채운다(LLM 손계산 expected 차단).
            sample_testcases=[
                SampleTestCase(input_text=text, expected_output="")
                for text in _generate_sample_inputs(
                    bp.io_schema,
                    state.run_id,
                    # 경계 샘플 1개 보장 — edge_case_semantics 류 invariant 가 있으면
                    # 마지막 샘플을 backbone 파생 퇴화 입력으로 교체 (BOJ 표준).
                    output_invariants=bp.output_invariants,
                )
            ],
        )
        return state.model_copy(update={"spec": spec})

    return node
