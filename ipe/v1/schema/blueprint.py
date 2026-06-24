"""Blueprint-first 모델링 layer 아티팩트 (Phase 3 M3).

blueprint-first 노선(`docs/rfc/phase3_blueprint-first-generation.md`): formal schema 를
**먼저 freeze** 하고, golden/brute/test 가 prose 가 아닌 그 schema 를 읽는다. narrative
는 마지막 렌더링 단계로 강등 + round-trip 으로 충실성 검증. 목적: 상관오독(메인 §7
핵심 위협)을 사후 게이트가 아니라 **생성 구조**로 방어.

artifacts:
- ``ProblemBlueprint``: Formalizer 가 freeze 하는 formal 계약 (io_schema +
  output_invariants + 숨은 reduction_core). frozen=True 자체가 freeze.
- ``Narrative``: late artifact — frozen blueprint 의 시나리오 렌더(은닉/직접).
- ``NarrativeFaithfulnessReport``: round-trip 결과 (왜곡=reject, 은닉=OK).

``ProblemSpec`` (problem_spec.py) 와의 관계: blueprint 은 freeze 되는 **상위 formal
계약**, ProblemSpec 은 그로부터 파생되는 solver/executor 입력 contract. ``generator_
contract`` (입력 생성기 제약) 은 입력 생성 milestone(M4)으로 이연.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .problem_spec import ConstraintRange, TargetAlgorithm

IOFieldType = Literal[
    "int",
    "int_array",
    "int_matrix",
    "float",
    "string",
    "bool",
    "weighted_edges",
    "tree_edges",
    "grid",
]

OutputType = Literal["int", "int_array", "float", "bool", "string", "yes_no"]


class GraphShape(BaseModel):
    """graph 타입 필드(weighted_edges/tree_edges)의 **구조 사실** — F6~F8 단일 진실원천.

    오늘날 이 사실들은 ``input_gen._serialize_weighted_edges``/``_backbone`` 안에
    **하드코딩**돼 있고 formalizer/narrative 프롬프트에 prose 규칙으로 **재진술**될
    뿐이다(O(N²) 모순 표면). IR 필드로 끌어올리면 직렬화기가 이것을 **READ**(단일
    진실)하고 narrative/QA/faithfulness 가 prose 규칙이 아니라 **기계 비교**로 검증한다.

    기본값은 현 직렬화기 상수와 **동일** → graph_shape 가 None 이거나 기본값이면 생성
    바이트가 byte-identical(formalizer 가 변주하기 전까지 무위험). ``directed`` 만은
    오늘날 **어디에도 결정돼 있지 않은** 잠재 모순(F8: 직렬화기는 ``u v w`` 방출 /
    DijkstraVerifier 는 directed 가정 / narrative 는 '양방향' 자유 서술 → 아무 게이트도
    못 잡음)이라 **필수**로 핀한다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    directed: bool = Field(
        ...,
        description=(
            "간선 방향성 (F8 잠재 모순 핀). True=단방향(u→v), False=양방향(u↔v). "
            "오늘날 어디에도 결정 안 됨 → 반드시 명시."
        ),
    )
    self_loops: bool = Field(
        default=False, description="자기 자신 간선(u==v) 허용 여부 (F6, 현 상수=False)"
    )
    multi_edges: bool = Field(
        default=True, description="같은 쌍 중복 간선 허용 여부 (F7, 현 상수=True)"
    )
    connectivity: Literal["connected", "maybe_disconnected"] = Field(
        default="maybe_disconnected",
        description="연결 보장 여부 (F7, weighted_edges 현 상수=maybe_disconnected)",
    )


class IOFieldSpec(BaseModel):
    """입력 한 필드의 formal 명세 — 타입 + 크기/값 범위 (prose 아님)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1, description="필드 이름 (예: 'N', 'edges')")
    type: IOFieldType
    size_range: ConstraintRange | None = Field(
        default=None, description="배열/행렬/그래프의 원소·정점·행 수 범위"
    )
    value_range: ConstraintRange | None = Field(
        default=None, description="수치 값 범위 (가중치/원소값 등)"
    )
    references: str | None = Field(
        default=None,
        description=(
            "정점/원소/행을 가리키는 **스칼라 int 참조** — 가리키는 collection 필드 "
            "이름. 설정 시 입력 생성기가 그 필드의 **실제 생성 크기**에 맞춰 "
            "[1, 실제크기] 1-indexed 로 생성한다(value_range·tier 무시). 정점 질의 "
            "s/t 가 V 와 무관하게 [1,2](trivial)·V 초과(범위밖 RTE)로 생성되던 결함의 "
            "구조적 해소 — 차원이 데이터 의존이라 정적 range 로 표현 불가하기 때문."
        ),
    )
    cols_range: ConstraintRange | None = Field(
        default=None,
        description=(
            "int_matrix/grid 의 **열 수(C) 범위** — size_range 는 행 수(N). 레코드가 "
            "고정 K개 속성을 가지면 [K,K] 로 고정한다. None 이면 열 수도 size_range "
            "에서(현행). 열 수가 무작위라 행별 속성 수가 흔들려 정해가 IndexError 로 "
            "깨지던 결함의 해소."
        ),
    )
    graph_shape: GraphShape | None = Field(
        default=None,
        description=(
            "graph 타입(weighted_edges/tree_edges) 의 구조 사실 — directed/self_loops/"
            "multi_edges/connectivity (F6~F8). None 이면 직렬화기가 현 상수(self-loop "
            "없음·다중간선 허용·연결 비보장)로 동작(byte-identical). 설정 시 직렬화기가 "
            "이것을 READ 하고 narrative/QA 가 기계 비교로 검증한다. 비-graph 필드엔 무의미."
        ),
    )
    description: str = ""


class IOSchema(BaseModel):
    """입력 구조/타입/범위 + 출력 타입/형식 — frozen blueprint 의 핵심 formal 면."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    inputs: tuple[IOFieldSpec, ...] = Field(..., min_length=1)
    output_type: OutputType
    output_format: str = Field(..., min_length=1, description="출력 인쇄 형식")
    indexing: Literal[0, 1] = Field(
        default=1,
        description=(
            "정점/원소 참조 인덱싱 규약 (F9). 1=1-indexed(현행·기본), 0=0-indexed. "
            "직렬화기(_backbone/참조 스칼라)와 input_format prose 의 단일 진실원천 — "
            "오늘날 _backbone 에 하드코딩(1)된 것을 IR 필드로 끌어올린다."
        ),
    )


class OutputInvariant(BaseModel):
    """출력이 항상 만족할 관계 — symbolic invariant 후보 (verifier schema 파생 단초)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(
        ...,
        min_length=1,
        description="invariant 종류 (예: non_negative / monotonic / sum_conserved)",
    )
    description: str = Field(..., min_length=1)


class StrategySeed(BaseModel):
    """Strategist(발산) 산출 — 은닉할 코어 + 합성 기법 + 위장 도메인 (M3 step2).

    blueprint-first 흐름의 첫 단계: ``seed_algorithm`` hint 를 받아 **무엇을 숨길지**
    (reduction_core)와 **어떤 현실 도메인으로 위장할지**(domain)를 정한다. Formalizer
    가 이 seed 를 받아 io_schema + output_invariants 를 붙여 ``ProblemBlueprint`` 로
    freeze — 알고리즘 결정은 여기서, 형식 동결은 Formalizer 에서 (책임 분리, Q1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reduction_core: TargetAlgorithm = Field(
        ..., description="숨은 알고리즘/환원 (solver 모름)"
    )
    composition: tuple[TargetAlgorithm, ...] = Field(
        default=(), description="reduction_core 외 추가 합성 기법"
    )
    domain: str = Field(
        ..., min_length=1, description="narrative 위장용 현실 도메인 (예: 'logistics')"
    )
    rationale: str = Field(
        default="", description="위장 전략 근거 (traceability, 선택)"
    )


class ProblemBlueprint(BaseModel):
    """blueprint-first 의 **frozen formal 계약** (M3 Formalizer 산출).

    golden/brute/test 가 이 schema 를 읽는다 (prose 아님). ``model_config``
    frozen=True 자체가 freeze 계약 — 생성 후 불변. ``reduction_core`` 는 숨은
    알고리즘(solver 는 모르고 은닉 narrative 만 봄, 내부 artifact 엔 명시).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reduction_core: TargetAlgorithm = Field(
        ..., description="숨은 알고리즘/환원 (solver 모름, 내부 명시)"
    )
    composition: tuple[TargetAlgorithm, ...] = Field(
        default=(), description="reduction_core 외 추가 합성 기법 (기법 합성)"
    )
    domain: str = Field(
        ..., min_length=1, description="narrative 렌더용 현실 도메인 (예: 'logistics')"
    )
    io_schema: IOSchema
    output_invariants: tuple[OutputInvariant, ...] = ()


class BlueprintFormalization(BaseModel):
    """Formalizer(동결) LLM 의 structured output — formal 면만 (io_schema + invariants).

    Formalizer 노드는 이 산출을 ``StrategySeed`` (reduction_core/composition/domain)
    와 결합해 ``ProblemBlueprint`` 로 freeze 한다. **알고리즘 결정 필드는 포함하지
    않음** — Strategist 가 정한 것을 노드가 구조적으로 carry-over 하여 Formalizer 가
    은닉 코어를 임의로 바꾸지 못하게 한다 (freeze 규율, 상관오독 §7 방어).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    io_schema: IOSchema
    output_invariants: tuple[OutputInvariant, ...] = ()


class Narrative(BaseModel):
    """late artifact — frozen blueprint 의 시나리오 렌더링 (M3 Narrative Author).

    ``hidden=True`` 면 알고리즘 은닉 렌더(full/B2B), ``False`` 면 직접 기술
    (canonical/B2C 토픽드릴). 은닉은 *생성 순서* 가 아니라 **렌더링 선택**.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario: str = Field(..., min_length=1, description="현실 시나리오 지문")
    hidden: bool = Field(..., description="알고리즘 은닉 렌더 여부")
    domain: str = Field(..., min_length=1)


class NarrativeDraft(BaseModel):
    """Narrative Author(창작) LLM 의 structured output — scenario 프로즈만 (M3 step3).

    Narrative 노드는 이 draft 에 ``hidden``(렌더 모드, graph config)과 ``domain``
    (frozen blueprint 에서 carry-over)을 스탬프해 ``Narrative`` 로 완성한다. LLM 은
    **시나리오 지문만** 쓰고 hidden/domain 은 노드가 authoritative — Formalizer 의
    carry-over 규율과 동일 (렌더 모드/도메인을 LLM 이 임의로 못 바꿈).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario: str = Field(..., min_length=1, description="현실 시나리오 지문 (은닉/직접)")


class NarrativeFaithfulnessReport(BaseModel):
    """round-trip 충실성 결과 (M3) — narrative 재형식화 schema vs frozen blueprint diff.

    정보 *은닉*(누락 의도)은 distortion 아님; 정보 *왜곡*(다른 문제 기술)만 reject.
    ``faithful=False`` 면 narrative 재생성 (싼 반복).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    faithful: bool = Field(
        ..., description="재형식화가 frozen blueprint 와 일치 (왜곡 없음)"
    )
    distortions: tuple[str, ...] = Field(
        default=(), description="왜곡 근거 (reject 사유, 사람이 읽는 설명)"
    )
