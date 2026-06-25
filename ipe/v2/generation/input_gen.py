"""결정론적 입력 생성 엔진 + GeneratorContract 순수 투영 — io_schema → 입력 (M4 step3).

``io_schema`` 에서 **순수 투영**한 ``GeneratorContract``(``derive_generator_contract`` —
scale_families + edge_cases)와 frozen ``io_schema``(필드 타입/범위)를 받아, **seeded RNG
로 결정론** 입력을 만든다. 같은 (contract, io_schema, seed) 면 항상 같은 입력 → 재현가능
채점셋. (계약은 Phase 3 전엔 Opus 가 저작했으나 io_schema 가 이미 결정하는 정보라 순수
파생으로 강등 — unrealizable 카테고리 reject 제거 + Opus 호출 1 삭제, RFC §4.)

직렬화 규약 (canonical, 필드별 줄 단위):
- ``int`` / ``bool`` / ``float`` : 한 줄 스칼라 (bool=1/0, float=소수4자리).
- ``string`` : 한 줄 영소문자 (길이=size, 최소 1 — 빈 입력 금지).
- ``int_array`` : ``N`` 줄 + ``N`` 개 공백구분 정수 줄 (N=0 면 ``0`` 만).
- ``int_matrix`` : ``R C`` 줄 + R 개의 (C 개 정수) 줄.
- ``weighted_edges`` (step3b) : ``V E`` 줄 + E 줄 ``u v w`` (**1-indexed**, self-loop
  없음, w=value_range). 랜덤 부착 backbone 으로 **연결 보장** + extra 간선(다중간선
  허용). bias: min=트리 밀도(E=V-1) / max=조밀 / empty=``1 0`` / disconnected=정확히
  두 컴포넌트(backbone ×2, E=V-2).
- ``tree_edges`` (step3b) : ``V`` 줄 + (V-1) 줄 ``u v`` — value_range 있으면
  ``u v w``. 랜덤 부착 트리(연결+무사이클 보장). empty=``1``.
- ``grid`` (step3b) : ``int_matrix`` 와 동일 규약 (의미 구분은 blueprint 몫).
- 여러 필드는 io_schema 순서로 줄 join.

graph 필드는 **self-contained** (V/E 헤더 포함) — V:int 필드를 따로 두는 분리 모델링
은 중복/모순을 낳으므로 formalizer prompt 가 단일 graph 필드로 유도한다.

정점/원소 참조 스칼라(s/t/질의 index)는 ``IOFieldSpec.references`` 로 가리키는
collection 필드명을 선언하면, 2-pass 직렬화가 그 필드의 **실제 생성 크기**에 맞춰
``[1, 실제크기]`` 1-indexed 로 생성한다(value_range·tier 무시). 정적 ConstraintRange
로는 데이터 의존 차원을 표현할 수 없어 s 가 V 와 무관하게 ``[1,2]``(trivial)·V 초과
(범위밖 RTE)로 생성되던 결함의 구조적 해소. int_matrix/grid 의 열 수는 ``cols_range``
로 행 수(size_range)와 분리 고정(레코드 고정 K 속성). 규약↔골든 파서 정합은 assembled
비율 anchor 로 실측(known item).

tier 적용: ScaleFamily.field_bounds(이름=필드명)는 스칼라의 **값**, sized 타입의
**크기**(graph 는 정점 수 V)를 그 tier 로 좁힌다. 원소/가중치 값은 io_schema 의
value_range. 참조 스칼라는 tier 를 보지 않는다(실제 크기에 바인딩).
"""

from __future__ import annotations

import hashlib
import math
import random
from typing import TYPE_CHECKING, Literal

from ipe.v1.schema import (
    ConstraintRange,
    EdgeCaseSpec,
    GeneratedTestCase,
    GeneratorContract,
    ScaleFamily,
)

if TYPE_CHECKING:
    from ipe.v1.schema import (
        IOFieldSpec,
        IOSchema,
        SequenceShape,
        StringShape,
    )

# 범위 미지정 시 기본값
_DEFAULT_SIZE = (1, 10)
_DEFAULT_VALUE = (0, 100)
_STRING_MIN_LEN = 1  # 빈 문자열 금지 (GeneratedTestCase.input_text min_length=1 보존)
_ALPHABET = "abcdefghijklmnopqrstuvwxyz"

# string_shape.alphabet → 실제 문자 풀 (StringBackbone 의미 사실과 짝, 이쪽은 직렬화 바이트).
# lowercase 는 현 상수 _ALPHABET 와 동일 객체 → 미핀/lowercase 면 byte-identical.
_ALPHABETS = {
    "lowercase": _ALPHABET,
    "uppercase": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "binary": "01",
    "dna": "ACGT",
    "alphanumeric": _ALPHABET + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
}

# 단일 입력의 총 원소 수(배열 N·그래프 간선 E·행렬 R*C·문자열 길이) 상한. LLM contract
# 가 V=200000 같은 상한을 줘도 결정론 클램프 — 패키지 비대화(케이스당 7.8MB·60케이스
# 88MB 실측) + 대형 배열 생성 OOM(SIGKILL) 을 한 레버로 차단. CP 대형 테스트 강도와
# 패키지/전송 실용성의 타협(≈수백 KB/입력); 조정은 이 상수만.
_MAX_ELEMENTS = 10000

# "disconnected" 는 graph 타입 전용 의미(두 컴포넌트) — 비-graph 필드에선 random 취급
_Bias = Literal["random", "empty", "min", "max", "disconnected"]


def seed_from_run_id(run_id: str) -> int:
    """run_id → 안정적 seed (내장 ``hash()`` 는 PYTHONHASHSEED 의존이라 비결정)."""
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


# ---------- canonical input_format 렌더 (step6) ----------
# 위 직렬화 규약의 사람/LLM용 prose. **이 모듈의 serializer 와 같은 파일에 두는
# 이유**: 규약이 바뀌면 렌더도 같이 바뀌어야 한다 (드리프트 = ratio 0.0 의 원인).

# 비-graph·비-indexing 타입의 고정 prose. graph 류(weighted_edges/tree_edges)와 참조
# 스칼라는 graph_shape/indexing 에서 **파생**하므로 여기 두지 않는다 (단일 진실원천).
_FORMAT_TEXT = {
    "int": "한 줄에 정수 하나.",
    "bool": "한 줄에 0 또는 1.",
    "float": "한 줄에 실수 하나 (소수 4자리).",
    "string": "한 줄에 영소문자 문자열.",
    "int_matrix": "첫 줄에 'R C'(행 수, 열 수), 이어서 R 줄에 각 C 개의 공백구분 정수.",
    "grid": "첫 줄에 'R C'(행 수, 열 수), 이어서 R 줄에 각 C 개의 공백구분 정수.",
}
# int_array 는 빈 수열(N=0) 절을 size_range.min 에서 파생해야 하므로 상수가 아니라
# ``_int_array_format(field)`` 로 렌더한다 — min≥1 스키마에 'N=0 이면…' 을 무조건 달면
# render_constraints 의 N∈[min,max](min≥1) 와 정면 충돌해 QA ambiguity reject 된다(실측).


def _vertex_index_phrase(indexing: int) -> str:
    """그래프 정점 번호 인덱싱 규약 prose (io_schema.indexing 단일 진실 투영)."""
    return "0..V-1 (0-indexed)" if indexing == 0 else "1..V (1-indexed)"


def _structural_clause(field: IOFieldSpec) -> str:
    """weighted_edges 구조 사실 prose (graph_shape 단일 진실 투영) — self-loop/다중간선/
    연결/방향성. shape=None 이면 현 상수(레거시; directedness 는 F8 미정이라 생략).
    """
    shape = field.graph_shape
    self_loops = shape.self_loops if shape is not None else False
    multi_edges = shape.multi_edges if shape is not None else True
    connectivity = shape.connectivity if shape is not None else "maybe_disconnected"
    parts = [
        "self-loop 가능" if self_loops else "self-loop 없음",
        "다중 간선 가능" if multi_edges else "다중 간선 없음(단순 그래프)",
        "연결 보장" if connectivity == "connected" else "연결 비보장(분리 컴포넌트 가능)",
    ]
    if shape is not None:  # directedness 는 핀됐을 때만 (None=레거시 F8 미정)
        parts.insert(0, "단방향(u→v)" if shape.directed else "양방향(u↔v)")
    return ", ".join(parts)


# 정렬성/중복 라벨 — **단일 진실원천**. format prose(``_sequence_clause``)와 narrative
# DATA(``SequenceBackbone.structural_facts``)가 둘 다 여기서 READ → 두 곳이 드리프트 불가.
# non_decreasing 은 수식형(a[i] ≤ a[i+1])으로만 표기 — 평문 '오름차순' 병기는 순증가
# (strictly ascending)로 읽혀 ``duplicates_allowed=True`` 와 충돌, QA ambiguity reject
# 됐다(binary_search 실측). 두 곳이 같은 라벨을 보므로 이 모호성이 한 곳에서만 닫힌다.
SORTEDNESS_LABEL = {
    "unsorted": "무정렬(임의 순서)",
    "non_decreasing": "비내림차순 정렬(a[i] ≤ a[i+1])",
    "strictly_increasing": "순증가 정렬(a[i] < a[i+1], 중복 없음)",
}
DUPLICATES_LABEL = {True: "중복 값 가능", False: "중복 값 없음(서로 다른 값)"}


def _sequence_clause(shape: SequenceShape) -> str:
    """int_array 정렬/중복 사실 prose (sequence_shape 단일 진실 투영 — ``SORTEDNESS_LABEL``/
    ``DUPLICATES_LABEL`` READ). format prose 는 직렬화 바이트와 **드리프트 금지** 라
    serializer 동거 — ``_serialize_int_array`` 가 같은 shape 를 READ 해 실제 정렬/distinct
    배열을 방출한다. structural_facts 와 같은 라벨을 공유 = 형식↔의미 무드리프트."""
    sort = SORTEDNESS_LABEL[shape.sortedness]
    if shape.sortedness == "strictly_increasing":
        return sort  # distinct 함축 — 중복 절 생략
    return f"{sort}, {DUPLICATES_LABEL[shape.duplicates_allowed]}"


def _string_clause(shape: StringShape) -> str:
    """string 문자 집합 prose (string_shape 단일 진실 투영). format prose 는 직렬화 바이트와
    드리프트 금지라 serializer 동거 — ``_string_alphabet`` 이 같은 alphabet 을 READ 한다."""
    return {
        "lowercase": "영소문자(a-z)",
        "uppercase": "영대문자(A-Z)",
        "binary": "이진(0/1)",
        "dna": "DNA 염기(A,C,G,T)",
        "alphanumeric": "영문자+숫자(a-zA-Z0-9)",
    }[shape.alphabet]


def _int_array_format(field: IOFieldSpec) -> str:
    """int_array 입력 형식 prose. 빈 수열(N=0) 절은 size_range 가 N=0 을 **실제 허용**할
    때만(min==0) 방출 — graph empty bias 가 size_range.min 을 존중하는 것과 동형으로,
    빈/최소 입력 의미를 ``size_range.min`` 단일소스에서 파생한다. min≥1 이면 절 없음(=
    render_constraints 의 N≥min 과 정합). 'N=0 이면…' 을 무조건 달면 N≥1 스키마에서
    constraints 와 모순돼 QA ambiguity reject 됐다(loop_accumulate/binary_search/lis 실측)."""
    base = "첫 줄에 원소 개수 N, 다음 줄에 N 개의 공백구분 정수"
    lo = field.size_range.min_value if field.size_range is not None else _DEFAULT_SIZE[0]
    return f"{base} (N=0 이면 '0' 한 줄만)." if lo == 0 else f"{base}."


def _render_field(field: IOFieldSpec, indexing: int) -> str:
    if _is_reference(field):
        # 참조 스칼라 — 가리키는 collection 의 크기에 묶인 값 (indexing base).
        lo, bound = ("0", "크기 미만") if indexing == 0 else ("1", "크기 이하")
        if field.reference_kind == "cardinality":
            # 개수/수량 (위치 인덱스 아님) — narrative 의 '개수' 서술과 정합.
            return (
                f"{field.name}: 한 줄에 정수 하나 — {field.references} 의 크기에 묶인 "
                f"개수/수량 ({lo} 이상 {field.references} 의 {bound}). 위치 인덱스가 아니다."
            )
        label = "0-indexed" if indexing == 0 else "1-indexed"
        return (
            f"{field.name}: 한 줄에 정수 하나 — {field.references} 의 원소/정점을 가리키는 "
            f"{label} 번호 ({lo} 이상 {field.references} 의 {bound})."
        )
    if field.type == "weighted_edges":
        return (
            f"{field.name}: 첫 줄에 'V E'(정점 수, 간선 수), 이어서 E 줄에 'u v w'(간선 "
            f"u-v 와 정수 가중치 w). 정점 번호는 {_vertex_index_phrase(indexing)}. "
            f"{_structural_clause(field)}."
        )
    if field.type == "tree_edges":
        edge_line = "'u v w'(간선과 정수 가중치)" if field.value_range else "'u v'"
        return (
            f"{field.name}: 첫 줄에 정점 수 V, 이어서 V-1 줄에 {edge_line}. "
            f"정점 번호는 {_vertex_index_phrase(indexing)}, 트리(연결·무사이클) 보장."
        )
    if field.type == "int_array":
        prose = _int_array_format(field)
        if field.sequence_shape is not None:
            return f"{field.name}: {prose} {_sequence_clause(field.sequence_shape)}."
        return f"{field.name}: {prose}"
    if field.type == "string" and field.string_shape is not None:
        return f"{field.name}: 한 줄에 {_string_clause(field.string_shape)} 문자열."
    if field.type in ("int_matrix", "grid") and field.cols_range is not None:
        cr = field.cols_range
        cols = (
            f"열 수 C={cr.min_value} 고정"
            if cr.min_value == cr.max_value
            else f"열 수 C∈[{cr.min_value}..{cr.max_value}]"
        )
        return (
            f"{field.name}: 첫 줄에 'R C'(행 수, {cols}), 이어서 R 줄에 각 C 개의 "
            "공백구분 정수."
        )
    return f"{field.name}: {_FORMAT_TEXT[field.type]}"


def describe_io_field(field: IOFieldSpec) -> str:
    """io_schema 한 필드의 설계용 요약 (design 프롬프트 공용) — name:type + 범위/참조/열수.

    spec_bridge·generator_designer 가 동일 포맷으로 LLM 에 필드를 기술하게 한다(DRY).
    참조 스칼라는 ``→refs X(1..|X|)`` 로, 고정 열 행렬은 ``cols[K..K]`` 로 노출해
    constraint/field_bounds 저작이 trivial [1,2] 대신 honest 한 관계를 쓰게 한다.
    """
    head = f"{field.name}:{field.type}"
    if _is_reference(field):
        kind = "개수" if field.reference_kind == "cardinality" else "위치번호"
        return f"{head} →refs {field.references}(1..|{field.references}|, {kind})"
    rng = ""
    if field.size_range is not None:
        rng += f" size[{field.size_range.min_value}..{field.size_range.max_value}]"
    if field.cols_range is not None:
        rng += f" cols[{field.cols_range.min_value}..{field.cols_range.max_value}]"
    if field.value_range is not None:
        rng += f" val[{field.value_range.min_value}..{field.value_range.max_value}]"
    return head + rng


# 컬렉션 size 차원의 관용 기호 + 한국어 라벨 (constraints 코드 파생용).
_SIZE_SYMBOL: dict[str, tuple[str, str]] = {
    "weighted_edges": ("V", "정점 수"),
    "tree_edges": ("V", "정점 수"),
    "int_array": ("N", "원소 개수"),
    "int_matrix": ("R", "행 수"),
    "grid": ("R", "행 수"),
}


def render_constraints(io_schema: IOSchema) -> list[ConstraintRange]:
    """io_schema → constraints 코드 파생 (LLM 저작 대체 — 단일 진실원천 투영).

    spec_bridge LLM 이 constraints 를 손저작하면 graph 의 V 를 E 로 오라벨하거나 V 범위
    자체를 누락하고, 참조 스칼라를 ``[1,2]`` 같은 리터럴로 적어 QA ambiguity 로 reject
    됐다(N=18 실측). io_schema 에서 코드로 파생하면 io_contract·parser·생성기와 **같은
    규약**을 보므로 드리프트가 사라진다. 참조 스칼라는 가리키는 컬렉션 크기에 묶고
    (``1 ≤ s ≤ V``), 행렬 고정 열은 ``C=K`` 로, 컬렉션은 size(V/N/R)+value 를 명시한다.
    """
    sized = {f.name: f for f in io_schema.inputs}
    base = io_schema.indexing  # F9 참조 인덱싱 base (1=현행 / 0=0-indexed)
    out: list[ConstraintRange] = []
    for f in io_schema.inputs:
        if _is_reference(f):
            ref = sized.get(f.references) if f.references is not None else None
            size_hi = (
                ref.size_range.max_value
                if ref is not None and ref.size_range is not None
                else 1
            )
            # 참조 상한을 컬렉션 크기 **기호**(V/N/R)로 — 정적 [1, V상한] 숫자가 아니라
            # 데이터 의존 '≤V'. input_format 의 '크기 이하' 서술과 numeric/symbolic 모순이
            # 나 graph 문제가 QA ambiguity 로 reject 되던 결함의 구조적 해소(실측 근본원인).
            symbolic = (
                _SIZE_SYMBOL[ref.type][0]
                if ref is not None
                and ref.type in _SIZE_SYMBOL
                and ref.size_range is not None
                else None
            )
            if symbolic is not None and base == 0:
                symbolic = f"{symbolic}-1"  # 0-indexed → '크기 미만' = [0, V-1]
            bound = "크기 미만" if base == 0 else "크기 이하"
            if f.reference_kind == "cardinality":
                # 개수/수량 (위치 인덱스 아님) — input_format·narrative 와 같은 의미.
                desc = (
                    f"{f.references} 의 크기에 묶인 개수/수량 "
                    f"({base} 이상 {f.references} 의 {bound})"
                )
            else:
                label = "0-indexed" if base == 0 else "1-indexed"
                desc = (
                    f"{f.references} 의 {label} 번호 "
                    f"({base} 이상 {f.references} 의 {bound})"
                )
            out.append(
                ConstraintRange(
                    name=f.name,
                    min_value=base,
                    max_value=base + size_hi - 1,
                    symbolic_max=symbolic,
                    description=desc,
                )
            )
            continue
        if f.type in ("int", "bool", "float"):
            if f.value_range is not None:
                out.append(
                    ConstraintRange(
                        name=f.name,
                        min_value=f.value_range.min_value,
                        max_value=f.value_range.max_value,
                        description=f"{f.name} 값",
                    )
                )
            continue
        # collection (array/matrix/graph)
        if f.size_range is not None and f.type in _SIZE_SYMBOL:
            sym, label = _SIZE_SYMBOL[f.type]
            out.append(
                ConstraintRange(
                    name=sym,
                    min_value=f.size_range.min_value,
                    max_value=f.size_range.max_value,
                    description=f"{f.name} 의 {label}",
                )
            )
        if f.cols_range is not None:
            out.append(
                ConstraintRange(
                    name="C",
                    min_value=f.cols_range.min_value,
                    max_value=f.cols_range.max_value,
                    description=f"{f.name} 의 열(속성) 수",
                )
            )
        if f.value_range is not None:
            is_graph = f.type in ("weighted_edges", "tree_edges")
            out.append(
                ConstraintRange(
                    name="w" if is_graph else f"{f.name}_값",
                    min_value=f.value_range.min_value,
                    max_value=f.value_range.max_value,
                    description="간선 가중치" if is_graph else f"{f.name} 원소 값",
                )
            )
    return out


def format_constraint(c: ConstraintRange) -> str:
    """ConstraintRange → ``name ∈ [min, upper]`` 텍스트 (constraints 블록 단일 렌더).

    데이터 의존 상한(참조 스칼라 등)은 ``symbolic_max``(예 'V')로 렌더해 input_format 의
    '크기 이하' 서술과 정합시킨다 — 정적 ``[1, V상한]`` 숫자 vs 기호 '≤V' 모순이 graph
    문제를 QA ambiguity 로 reject 시키던 결함의 해소. symbolic_max=None 이면 max_value
    숫자(현행). qa_reviewer·published 패키지가 동일 렌더를 쓰게 한다(드리프트 차단).
    """
    upper = c.symbolic_max if c.symbolic_max is not None else str(c.max_value)
    return f"{c.name} ∈ [{c.min_value}, {upper}]"


def render_input_format(io_schema: IOSchema) -> str:
    """io_schema → 입력 형식 명세 prose — ``generate_inputs`` 직렬화와 동일 규약.

    spec_bridge 가 ``io_contract.input_format`` 으로 freeze 해 golden 파서·sample·
    생성 입력이 **한 규약**을 보게 한다 (M4 step6 — dijkstra anchor ratio 0.0 로
    실증된 직렬화↔파서 불일치의 구조적 해소).
    """
    parts = [_render_field(f, io_schema.indexing) for f in io_schema.inputs]
    if len(parts) == 1:
        return f"표준입력으로 주어진다. {parts[0]}"
    numbered = "\n".join(f"{i + 1}) {p}" for i, p in enumerate(parts))
    return "표준입력으로 다음 필드들이 순서대로 줄 단위 주어진다:\n" + numbered


# ---------- GeneratorContract 순수 투영 (Phase 3 — generator_designer LLM 대체) ----------
# io_schema 가 이미 결정하는 정보(규모 tier·실현가능 퇴화)를 **결정론 파생**한다. LLM 의
# 자유도는 어차피 ``_edge_bias`` 가 5개 bias 로 접으므로 추가 정보 없이 unrealizable
# 카테고리 리스크만 더했다 — 순수 투영으로 그 리스크를 0 으로 (RFC §4, F17-18).

# scale tier 가 크기를 좁히는 sized 타입 (입력 규모를 크기가 결정). 스칼라(int/bool/
# float)·참조는 제외. validator 의 _COLLECTION_TYPES + string(문자열 길이도 규모).
_SIZED_TYPES = (
    "int_array",
    "int_matrix",
    "grid",
    "string",
    "weighted_edges",
    "tree_edges",
)

# scale tier 당 케이스 수 — small 은 손검증·정확성용 다수, large 는 성능경계용 소수
# (채점 비용 바운드). sized 필드 없는 스칼라 schema 는 단일 nominal family.
_SMALL_CASES = 3
_LARGE_CASES = 2
_NOMINAL_CASES = 4


def _effective_size(field: IOFieldSpec) -> tuple[int, int]:
    """필드의 크기 범위 — size_range 가 있으면 그것, 없으면 기본(validator 가 collection
    의 size_range 누락을 이미 차단하므로 실전 경로엔 항상 존재; 방어적 기본)."""
    return _range_or(field.size_range, _DEFAULT_SIZE)


def _tier_split(lo: int, hi: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """[lo, hi] → (small, large) log-spaced 하위 구간. 기하 중앙(isqrt)으로 분할 —
    선형 중앙이 아니라 로그 중앙이라 CP 의 지수적 규모 스펙트럼을 고루 덮는다.
    hi<=lo(단일값 범위)면 두 tier 모두 그 점."""
    if hi <= lo:
        return (lo, hi), (lo, hi)
    mid = math.isqrt(max(lo, 1) * hi)  # 기하평균 근사 (lo=0 보호)
    mid = max(lo, min(mid, hi))
    return (lo, mid), (mid, hi)


def _is_sized(field: IOFieldSpec) -> bool:
    """scale tier 가 크기를 좁히는 필드인가 (sized 타입 — 스칼라/참조 제외)."""
    return field.type in _SIZED_TYPES


def _empty_safe(field: IOFieldSpec) -> bool:
    """이 필드에서 'empty' 퇴화가 **실제로 빈/간선없는 입력**으로 실현되는가 (카테고리명↔입력 정합).

    weighted_edges 는 empty 가 'V_min 0'(정점 하한·간선 0)으로 size_min 존중 + 항상 간선
    없음 → True. tree_edges 는 V_min<=1 일 때만 단일 정점('1', 간선 0); V_min>=2 면
    직렬화기가 V_min-1 간선의 최소 트리를 내므로 '간선없는'이 거짓이라 미방출(F18 류
    카테고리명↔입력 불일치 방지). array/matrix/grid 는 empty 가 크기 0 이라 size_min==0
    일 때만 제약(크기≥min)과 정합. string 은 최소 길이 1 강제라 'empty' 불가 → 제외.
    """
    if field.type == "weighted_edges":
        return True
    if field.type == "tree_edges":
        return _effective_size(field)[0] <= 1
    if field.type in ("int_array", "int_matrix", "grid"):
        return _effective_size(field)[0] == 0
    return False


def _separable_graph(field: IOFieldSpec) -> bool:
    """'disconnected' 퇴화가 실현 가능한 필드인가 — 연결 비보장 weighted_edges 만.

    트리(tree_edges)는 정의상 연결이라 분리 불가. weighted_edges 도 graph_shape.
    connectivity=='connected' 로 핀되면 직렬화기가 단일 컴포넌트를 강제(분리 미실현).
    shape=None(레거시)·maybe_disconnected 면 분리 가능 → disconnected bias 가 두 컴포넌트.
    """
    if field.type != "weighted_edges":
        return False
    shape = field.graph_shape
    return shape is None or shape.connectivity == "maybe_disconnected"


def derive_scale_families(io_schema: IOSchema) -> tuple[ScaleFamily, ...]:
    """io_schema → scale tier 들 (순수 투영, LLM 저작 대체).

    sized 필드(array/matrix/grid/string/graph)가 있으면 각 필드의 size_range 를
    log-spaced 로 'small'/'large' 두 tier 로 좁힌다 (field_bounds 이름=필드명 → 직렬화기가
    크기로 적용). sized 필드가 없으면(스칼라 only) 좁힐 규모가 없어 단일 'nominal' family
    (전체 범위 random). 항상 >=1 family (GeneratorContract 계약 보존).
    """
    sized = [f for f in io_schema.inputs if _is_sized(f)]
    if not sized:
        return (ScaleFamily(name="nominal", case_count=_NOMINAL_CASES),)
    small_bounds: list[ConstraintRange] = []
    large_bounds: list[ConstraintRange] = []
    for f in sized:
        lo, hi = _effective_size(f)
        (s_lo, s_hi), (l_lo, l_hi) = _tier_split(lo, hi)
        small_bounds.append(
            ConstraintRange(name=f.name, min_value=s_lo, max_value=s_hi)
        )
        large_bounds.append(
            ConstraintRange(name=f.name, min_value=l_lo, max_value=l_hi)
        )
    return (
        ScaleFamily(
            name="small",
            case_count=_SMALL_CASES,
            field_bounds=tuple(small_bounds),
            description="소규모 — 손검증·정확성",
        ),
        ScaleFamily(
            name="large",
            case_count=_LARGE_CASES,
            field_bounds=tuple(large_bounds),
            description="대규모 — 성능·경계",
        ),
    )


def derive_edge_cases(io_schema: IOSchema) -> tuple[EdgeCaseSpec, ...]:
    """io_schema → **실현 가능한** 퇴화 edge case 만 (순수 투영, LLM 저작 대체).

    각 이름은 ``_edge_bias`` 가 인식하는 bias 로 매핑되고 직렬화기가 그 입력을 실제로
    만든다 — LLM 이 만들 수 없는 카테고리(self_loop·특정 위상 등)를 채점셋 이름에 남겨
    형식 계약과 모순시키던 F18 reject 클래스를 구조적으로 제거(N=18 실측). 집합 =
    {min_size/max_size 크기 경계, empty(안전시), disconnected(분리가능 그래프)}.
    """
    sized = [f for f in io_schema.inputs if _is_sized(f)]
    edges: list[EdgeCaseSpec] = []
    if sized:
        edges.append(EdgeCaseSpec(name="min_size", description="하한: 최소 규모 입력"))
        edges.append(
            EdgeCaseSpec(name="max_size", description="상한: 최대 규모 입력 (성능·경계)")
        )
    # empty 는 empty bias 가 **전 필드에 동시 적용**되므로 모든 sized 필드가 안전할 때만 —
    # 하나라도 min>0 비-graph collection(또는 문자열)이 있으면 그 필드가 크기 0 으로
    # 제약(크기≥min)을 위반한다 (graph 는 V_min 존중·항상 안전). min/max/disconnected 는
    # 비-graph 필드에서 경계값/random 으로 처리돼 위반 불가라 이 제약이 없다.
    if sized and all(_empty_safe(f) for f in sized):
        edges.append(EdgeCaseSpec(name="empty", description="퇴화: 빈/간선없는 최소 입력"))
    if any(_separable_graph(f) for f in io_schema.inputs):
        edges.append(
            EdgeCaseSpec(name="disconnected", description="분리: 비연결 그래프(도달 불가)")
        )
    return tuple(edges)


def derive_generator_contract(io_schema: IOSchema) -> GeneratorContract:
    """io_schema → GeneratorContract (scale tiers + 실현가능 edge), 순수 투영.

    ``generator_designer`` 의 Opus 호출을 대체한다(RFC §4 Phase 3). determinism_seed=None
    → input_generator 가 run_id 파생 seed 사용(재현가능). 계약이 io_schema 의 함수이므로
    narrative/spec 등 다른 투영과 모순될 수 없다 (consistency-by-construction).
    """
    return GeneratorContract(
        scale_families=derive_scale_families(io_schema),
        edge_cases=derive_edge_cases(io_schema),
    )


# ---------- 퇴화 입력 파생 (Phase 5a — reconcile differential 의 Tier B 유일성 probe) ----------
# RFC §3.3/§6: io_schema 의 realizable-degeneracy 집합을 결정론 직렬화해 reconcile
# differential 에 더한다. 골든들이 퇴화 입력에 합의 → 그 엣지 well-posed·출력 operational
# 정의; 불합의 → 그 입력이 witness 인 ill-posed IR. 채점셋(generate_inputs)과 달리 **고정
# seed**(canonical — IR 만의 함수, run 무관 재현). graph(graph_shape)·sequence(sequence_
# shape)·string(string_shape) 백본이 호출; 어느 백본도 소유 안 한 타입은 NullBackbone=빈 튜플.
_EDGE_SEED = 0


def derive_degenerate_inputs(io_schema: IOSchema) -> tuple[tuple[str, str, str], ...]:
    """io_schema → 실현가능 퇴화 입력 ``(name, input_text, rationale)`` 들 (순수 투영).

    - **min**: sized 필드가 있으면 — 최소 규모 입력(질의 참조 스칼라는 ``min`` bias 로
      모두 하한=동일 정점에 수렴 → s==t 동치 케이스도 겸함). 경계 출력 의미를 probe.
    - **unreachable**: 분리가능 graph(``maybe_disconnected`` weighted_edges)면 — 두
      컴포넌트 분리 그래프. 도달 불가 가능성의 출력 의미를 probe (graph 의 핵심 ill-posed
      축: 도달 불가 시 답이 유일하게 정의되는가).

    각 입력은 ``_serialize_inputs`` 로 **고정 seed** 직렬화 — 같은 io_schema 면 항상 같은
    입력이라 (reconcile 가 diff 한 입력) == (edge_filler 가 golden 으로 채우는 입력). 직렬화는
    채점셋 생성기와 같은 규약을 쓰므로 골든 파서와 정합(format-valid, 의미만 퇴화). 퇴화가
    없으면(스칼라 only schema 등) 빈 튜플.
    """
    sized = [f for f in io_schema.inputs if _is_sized(f)]
    out: list[tuple[str, str, str]] = []
    if sized:
        text = _serialize_inputs(io_schema, {}, random.Random(_EDGE_SEED), bias="min")
        out.append(
            (
                "min",
                text,
                "최소 규모 입력(질의 정점 하한·동일 경계) — 경계 출력 의미",
            )
        )
    if any(_separable_graph(f) for f in io_schema.inputs):
        text = _serialize_inputs(
            io_schema, {}, random.Random(_EDGE_SEED), bias="disconnected"
        )
        out.append(
            (
                "unreachable",
                text,
                "분리 그래프(컴포넌트 2개) — 도달 불가 가능성의 출력 의미",
            )
        )
    return tuple(out)


def generate_inputs(
    contract: GeneratorContract,
    io_schema: IOSchema,
    *,
    seed: int,
) -> tuple[GeneratedTestCase, ...]:
    """contract + io_schema → 결정론 입력들 (expected=None pending).

    scale_families 각 tier 에서 ``case_count`` 개 + edge_cases 각 1 개. category 는
    출처(tier/edge name). 같은 seed → 같은 결과.
    """
    rng = random.Random(seed)
    cases: list[GeneratedTestCase] = []
    for family in contract.scale_families:
        tier_bounds = {cr.name: cr for cr in family.field_bounds}
        for _ in range(family.case_count):
            text = _serialize_inputs(io_schema, tier_bounds, rng, bias="random")
            cases.append(GeneratedTestCase(input_text=text, category=family.name))
    for edge in contract.edge_cases:
        text = _serialize_inputs(io_schema, {}, rng, bias=_edge_bias(edge.name))
        cases.append(GeneratedTestCase(input_text=text, category=edge.name))
    return tuple(cases)


def _is_reference(field: IOFieldSpec) -> bool:
    """참조 스칼라 여부 — int 타입 + references 지정 (방어: 비-int references 는 무시)."""
    return field.type == "int" and field.references is not None


def _serialize_inputs(
    io_schema: IOSchema,
    tier_bounds: dict[str, ConstraintRange],
    rng: random.Random,
    *,
    bias: _Bias,
) -> str:
    """2-pass — 비참조 필드 먼저(실제 addressable 크기 기록) → 참조 스칼라를 그 크기에
    바인딩. 참조 없는 schema 는 1-pass 와 동일 rng 순서(기존 출력 보존). 선언 순서로 join.
    """
    indexing = io_schema.indexing  # F9 단일 진실원천 (정점/원소 참조 인덱싱 base)
    texts: dict[str, str] = {}
    sizes: dict[str, int] = {}
    deferred: list[IOFieldSpec] = []
    for f in io_schema.inputs:
        if _is_reference(f):
            deferred.append(f)  # pass 2 — 참조 대상 크기 확정 후 생성
            continue
        text, size = _serialize_field(
            f, tier_bounds.get(f.name), rng, bias=bias, indexing=indexing
        )
        texts[f.name] = text
        if size is not None:
            sizes[f.name] = size
    for f in deferred:
        ref_size = sizes.get(f.references) if f.references is not None else None
        texts[f.name] = _serialize_reference(ref_size, bias, rng, indexing=indexing)
    return "\n".join(texts[f.name] for f in io_schema.inputs)


def _serialize_field(
    field: IOFieldSpec,
    tier_bound: ConstraintRange | None,
    rng: random.Random,
    *,
    bias: _Bias,
    indexing: int,
) -> tuple[str, int | None]:
    """필드 직렬화 → (text, addressable 크기). 크기는 참조 스칼라가 바인딩할 대상
    (배열 N · 행렬 R · 그래프 V); 순수 스칼라(int/bool/float)는 None. ``indexing`` 은
    graph 정점 번호 base (비-graph 타입은 무관).
    """
    t = field.type
    if t == "int":
        return str(_pick_value(_value_bounds(field, tier_bound), bias, rng)), None
    if t == "bool":
        return ("1" if _bool_value(bias, rng) else "0"), None
    if t == "float":
        lo, hi = _value_bounds(field, tier_bound)
        return f"{_pick_float(lo, hi, bias, rng):.4f}", None
    if t == "string":
        n = max(_pick_size(_size_bounds(field, tier_bound), bias, rng), _STRING_MIN_LEN)
        n = min(n, _MAX_ELEMENTS)  # 길이 캡
        pool = _string_alphabet(field.string_shape)
        return "".join(rng.choice(pool) for _ in range(n)), n
    if t == "int_array":
        return _serialize_int_array(field, tier_bound, rng, bias=bias)
    if t in ("int_matrix", "grid"):  # grid = int_matrix 와 동일 canonical 규약
        return _serialize_int_matrix(field, tier_bound, rng, bias=bias)
    if t == "weighted_edges":
        return _serialize_weighted_edges(field, tier_bound, rng, bias=bias, indexing=indexing)
    if t == "tree_edges":
        return _serialize_tree_edges(field, tier_bound, rng, bias=bias, indexing=indexing)
    msg = f"io_type '{t}' 미지원"
    raise NotImplementedError(msg)


def _serialize_reference(
    ref_size: int | None, bias: _Bias, rng: random.Random, *, indexing: int
) -> str:
    """참조 스칼라 값 — 참조 대상의 **실제 크기**에 바인딩 [base, base+size-1].

    인덱싱 base = ``indexing`` (1=1-indexed 현행 / 0=0-indexed). value_range·tier 를
    보지 않는다 (정적 range 로 표현 불가한 데이터 의존 차원). dangling/빈 컬렉션이면
    size=1 로 안전 default(crash 회피). bias 는 경계 선택(min/empty→base, max→상한).
    """
    size = max(ref_size if ref_size is not None else 1, 1)
    base = indexing
    return str(_pick_value((base, base + size - 1), bias, rng))


def _serialize_int_array(
    field: IOFieldSpec,
    tier_bound: ConstraintRange | None,
    rng: random.Random,
    *,
    bias: _Bias,
) -> tuple[str, int]:
    n = _pick_size(_size_bounds(field, tier_bound), bias, rng)
    if n <= 0:
        return "0", 0
    n = min(n, _MAX_ELEMENTS)  # 원소 총량 캡 (패키지 비대화/생성 OOM 차단)
    lo, hi = _element_bounds(field)
    values = _sequence_values(field.sequence_shape, n, lo, hi, rng)
    vals = " ".join(str(v) for v in values)
    return f"{len(values)}\n{vals}", len(values)  # 크기 = 원소 개수 N (참조 바인딩 대상)


def _sequence_values(
    shape: SequenceShape | None,
    n: int,
    lo: int,
    hi: int,
    rng: random.Random,
) -> list[int]:
    """int_array 원소값 — ``sequence_shape`` 의 sortedness/duplicates 를 honor (G1a).

    - shape=None **또는** (unsorted·duplicates_allowed): 현 동작과 **동일 rng 추출**
      순서/개수 → byte-identical 경로 (핀 안 했거나 현 상수와 동일하면 무변).
    - distinct(strictly_increasing **또는** duplicates_allowed=False): 서로 다른 값을
      표본추출. 범위가 n 보다 좁으면 범위 크기로 캡 — 실현가능성(같은 값 없이 n 개를
      못 담으면 담을 수 있는 만큼만).
    - 정렬 요구(non_decreasing/strictly_increasing): 추출 후 오름차순 정렬.

    정렬/distinct 가 켜진 경로는 byte-identical 대상이 아니므로(핀된 의도적 변주) rng
    추출 방식이 달라도 무방 — 고정 seed 면 여전히 결정론.
    """
    if shape is None or (shape.sortedness == "unsorted" and shape.duplicates_allowed):
        return [rng.randint(lo, hi) for _ in range(n)]  # byte-identical 경로
    distinct = shape.sortedness == "strictly_increasing" or not shape.duplicates_allowed
    if distinct:
        span = hi - lo + 1
        values = rng.sample(range(lo, hi + 1), min(n, span))  # 서로 다른 값
    else:
        values = [rng.randint(lo, hi) for _ in range(n)]
    if shape.sortedness in ("non_decreasing", "strictly_increasing"):
        values.sort()
    return values


def _string_alphabet(shape: StringShape | None) -> str:
    """string 필드 문자 풀 — ``string_shape.alphabet`` 을 honor (G2). None(미핀) 또는
    lowercase 면 현 상수 ``_ALPHABET``(a-z) → byte-identical(같은 풀에서 같은 rng.choice).
    """
    if shape is None:
        return _ALPHABET
    return _ALPHABETS[shape.alphabet]


def _cap_matrix(r: int, c: int) -> tuple[int, int]:
    """행렬 원소 총량 R*C 를 _MAX_ELEMENTS 로 캡 (결정론, 큰 차원부터 축소)."""
    r = min(r, _MAX_ELEMENTS)
    c = min(c, _MAX_ELEMENTS)
    if r * c > _MAX_ELEMENTS:
        c = max(1, _MAX_ELEMENTS // r)
    return r, c


def _serialize_int_matrix(
    field: IOFieldSpec,
    tier_bound: ConstraintRange | None,
    rng: random.Random,
    *,
    bias: _Bias,
) -> tuple[str, int]:
    size = _size_bounds(field, tier_bound)
    r = _pick_size(size, bias, rng)
    # 열 수: cols_range 가 있으면 그 범위(레코드 고정 K 속성), 없으면 size 와 동일(현행).
    cols = _range_or(field.cols_range, size)
    c = _pick_size(cols, bias, rng)
    if r <= 0 or c <= 0:
        return f"{max(r, 0)} {max(c, 0)}", max(r, 0)
    r, c = _cap_matrix(r, c)  # R*C 총량 캡
    lo, hi = _element_bounds(field)
    rows = "\n".join(
        " ".join(str(rng.randint(lo, hi)) for _ in range(c)) for _ in range(r)
    )
    return f"{r} {c}\n{rows}", r  # 크기 = 행 수 R (참조 바인딩 대상)


# ---------- graph serialization (step3b) ----------


def _backbone(start: int, end: int, rng: random.Random) -> list[tuple[int, int]]:
    """정점 ``start..end`` 를 잇는 랜덤 부착 트리 간선 — 연결+무사이클 보장.

    각 정점 i 를 이미 존재하는 정점(start..i-1) 중 하나에 붙인다 (self-loop 불가능).
    """
    return [(rng.randint(start, i - 1), i) for i in range(start + 1, end + 1)]


def _edge_key(u: int, t: int, *, directed: bool) -> tuple[int, int]:
    """multi_edges=False(단순 그래프) 중복 판정 키 — directed 면 순서쌍, undirected 면 무순쌍."""
    return (u, t) if directed else (min(u, t), max(u, t))


def _graph_vertex_count(
    field: IOFieldSpec,
    tier_bound: ConstraintRange | None,
    rng: random.Random,
    bias: _Bias,
) -> int:
    """graph 정점 수 V — size 범위에서. disconnected 는 크기 자체는 random."""
    size_bias: _Bias = "random" if bias == "disconnected" else bias
    return max(_pick_size(_size_bounds(field, tier_bound), size_bias, rng), 1)


def _serialize_weighted_edges(
    field: IOFieldSpec,
    tier_bound: ConstraintRange | None,
    rng: random.Random,
    *,
    bias: _Bias,
    indexing: int,
) -> tuple[str, int]:
    """``V E`` + E 줄 ``u v w``. backbone 연결 + bias 별 밀도/구조. 구조 사실은
    ``field.graph_shape`` 에서 READ (단일 진실) — None 이면 현 상수(self-loop 없음·
    다중간선 허용·연결 비보장)로 동작(byte-identical).

    반환 크기 = 정점 수 V (참조 스칼라 s/t 가 [base, base+V-1] 로 바인딩할 대상).
    정점 번호 base = ``indexing`` (1=1-indexed 현행 / 0=0-indexed).
    """
    shape = field.graph_shape
    self_loops = shape.self_loops if shape is not None else False
    multi_edges = shape.multi_edges if shape is not None else True
    # directed 는 간선 방출엔 무관(``u v w`` 동일) — multi_edges=False 중복 판정에만 쓰임.
    directed = shape.directed if shape is not None else True
    connected = shape.connectivity == "connected" if shape is not None else False
    base = indexing
    if bias == "empty":
        # 퇴화 최소 그래프 — 단, size_range.min 을 존중(V≥2 스키마면 '2 0').
        # 하한 무시하고 V=1 을 내면 constraints(V≥min)와 모순돼 QA reject(N=18 실측).
        vmin = max(_size_bounds(field, tier_bound)[0], 1)
        return f"{vmin} 0", vmin  # V_min 정점, 간선 0 (헤더는 카운트라 indexing 무관)
    v = _graph_vertex_count(field, tier_bound, rng, bias)
    v = min(v, _MAX_ELEMENTS // 2)  # E ≤ 2V → 간선 총량 캡
    if bias == "disconnected" and not connected:
        v = max(v, 2)  # 두 컴포넌트가 가능한 최소
        half = (v + 1) // 2
        edges = _backbone(base, base + half - 1, rng) + _backbone(
            base + half, base + v - 1, rng
        )
    else:  # connected 면 disconnected bias 도 단일 컴포넌트(구조 사실 우선)
        edges = _backbone(base, base + v - 1, rng)
        extra = 0 if bias == "min" else (v if bias == "max" else rng.randint(0, v))
        if v >= 2:
            seen = (
                {_edge_key(u, t, directed=directed) for u, t in edges}
                if not multi_edges
                else None
            )
            for _ in range(extra):
                u = rng.randint(base, base + v - 1)
                if self_loops:
                    t = rng.randint(base, base + v - 1)  # 자기 간선 포함 가능
                else:
                    t = rng.randint(base, base + v - 2)
                    if t >= u:  # self-loop 회피 (다중간선은 허용)
                        t += 1
                if seen is not None:  # multi_edges=False → 중복/자기간선 skip
                    key = _edge_key(u, t, directed=directed)
                    if (not self_loops and u == t) or key in seen:
                        continue
                    seen.add(key)
                edges.append((u, t))
    lo, hi = _element_bounds(field)  # value_range = 가중치
    lines = [f"{u} {t} {rng.randint(lo, hi)}" for u, t in edges]
    return "\n".join([f"{v} {len(edges)}", *lines]), v


def _serialize_tree_edges(
    field: IOFieldSpec,
    tier_bound: ConstraintRange | None,
    rng: random.Random,
    *,
    bias: _Bias,
    indexing: int,
) -> tuple[str, int]:
    """``V`` + (V-1) 줄 ``u v`` (value_range 있으면 ``u v w``). 랜덤 부착 트리.

    트리는 정의상 연결·무사이클·단순 → graph_shape 의 connectivity/multi_edges/
    self_loops 는 구조적으로 고정(직렬화기가 보장). 정점 번호 base = ``indexing``.
    disconnected bias 는 크기 random 으로만 작용. 반환 크기 = V.
    """
    base = indexing
    if bias == "empty":
        # 최소 트리 — size_range.min 존중(V≥2 스키마면 단일 정점이 아니라 V_min 트리).
        v = max(_size_bounds(field, tier_bound)[0], 1)
    else:
        v = _graph_vertex_count(field, tier_bound, rng, bias)
    v = min(v, _MAX_ELEMENTS)  # V-1 간선
    if v <= 1:
        return "1", 1  # 단일 정점 트리 (헤더는 카운트라 indexing 무관)
    edges = _backbone(base, base + v - 1, rng)
    if field.value_range is not None:
        lo, hi = _element_bounds(field)
        lines = [f"{u} {t} {rng.randint(lo, hi)}" for u, t in edges]
    else:
        lines = [f"{u} {t}" for u, t in edges]
    return "\n".join([str(v), *lines]), v


# ---------- bounds resolution ----------


def _range_or(cr: ConstraintRange | None, default: tuple[int, int]) -> tuple[int, int]:
    return (cr.min_value, cr.max_value) if cr is not None else default


def _value_bounds(
    field: IOFieldSpec, tier_bound: ConstraintRange | None
) -> tuple[int, int]:
    """스칼라 값 범위 — tier 가 우선 좁힘, 없으면 io_schema.value_range."""
    cr = tier_bound if tier_bound is not None else field.value_range
    return _range_or(cr, _DEFAULT_VALUE)


def _size_bounds(
    field: IOFieldSpec, tier_bound: ConstraintRange | None
) -> tuple[int, int]:
    """sized 타입 크기 범위 — tier 가 우선 좁힘, 없으면 io_schema.size_range."""
    cr = tier_bound if tier_bound is not None else field.size_range
    return _range_or(cr, _DEFAULT_SIZE)


def _element_bounds(field: IOFieldSpec) -> tuple[int, int]:
    """array/matrix 원소 값 범위 — io_schema.value_range (tier 무관)."""
    return _range_or(field.value_range, _DEFAULT_VALUE)


# ---------- bias picks ----------


def _pick_value(bounds: tuple[int, int], bias: _Bias, rng: random.Random) -> int:
    lo, hi = bounds
    if bias in ("min", "empty"):
        return lo
    if bias == "max":
        return hi
    return rng.randint(lo, hi)


def _pick_float(lo: int, hi: int, bias: _Bias, rng: random.Random) -> float:
    if bias in ("min", "empty"):
        return float(lo)
    if bias == "max":
        return float(hi)
    return rng.uniform(lo, hi)


def _pick_size(bounds: tuple[int, int], bias: _Bias, rng: random.Random) -> int:
    lo, hi = bounds
    if bias == "empty":
        return 0
    if bias == "min":
        return lo
    if bias == "max":
        return hi
    return rng.randint(lo, hi)


def _bool_value(bias: _Bias, rng: random.Random) -> bool:
    if bias in ("min", "empty"):
        return False
    if bias == "max":
        return True
    return rng.randint(0, 1) == 1


def _edge_bias(name: str) -> _Bias:
    """edge 케이스 이름 → boundary 전략 (generic keyword 해석)."""
    low = name.lower()
    # graph 전용 의미가 가장 구체적 — 먼저 매칭 ('disconnected_large' 등 복합어 보호)
    if any(k in low for k in ("disconnect", "unreachable", "isolated")):
        return "disconnected"
    if any(k in low for k in ("empty", "zero", "null")):
        return "empty"
    if any(k in low for k in ("max", "large", "stress", "big", "full", "huge", "upper")):
        return "max"
    # single/one/min/small/lower/tiny + 미인식 → 하한 경계
    return "min"
