"""결정론적 입력 생성 엔진 — GeneratorContract + io_schema → 입력 문자열 (M4 step3).

LLM(step2)이 설계한 ``GeneratorContract``(scale_families + edge_cases)와 frozen
``io_schema``(필드 타입/범위)를 받아, **seeded RNG 로 결정론** 입력을 만든다. 같은
(contract, io_schema, seed) 면 항상 같은 입력 → 재현가능 채점셋.

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
import random
from typing import TYPE_CHECKING, Literal

from ipe.v1.schema import ConstraintRange, GeneratedTestCase

if TYPE_CHECKING:
    from ipe.v1.schema import (
        GeneratorContract,
        IOFieldSpec,
        IOSchema,
    )

# 범위 미지정 시 기본값
_DEFAULT_SIZE = (1, 10)
_DEFAULT_VALUE = (0, 100)
_STRING_MIN_LEN = 1  # 빈 문자열 금지 (GeneratedTestCase.input_text min_length=1 보존)
_ALPHABET = "abcdefghijklmnopqrstuvwxyz"

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
    "int_array": (
        "첫 줄에 원소 개수 N, 다음 줄에 N 개의 공백구분 정수 "
        "(N=0 이면 '0' 한 줄만)."
    ),
    "int_matrix": "첫 줄에 'R C'(행 수, 열 수), 이어서 R 줄에 각 C 개의 공백구분 정수.",
    "grid": "첫 줄에 'R C'(행 수, 열 수), 이어서 R 줄에 각 C 개의 공백구분 정수.",
}


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


def _render_field(field: IOFieldSpec, indexing: int) -> str:
    if _is_reference(field):
        # 참조 스칼라 — 가리키는 collection 의 원소/정점 번호 (indexing base).
        lo, bound = ("0", "크기 미만") if indexing == 0 else ("1", "크기 이하")
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
        return f"{head} →refs {field.references}(1..|{field.references}|)"
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
            label = "0-indexed" if base == 0 else "1-indexed"
            bound = "크기 미만" if base == 0 else "크기 이하"
            out.append(
                ConstraintRange(
                    name=f.name,
                    min_value=base,
                    max_value=base + size_hi - 1,
                    description=(
                        f"{f.references} 의 {label} 번호 "
                        f"({base} 이상 {f.references} 의 {bound})"
                    ),
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


def render_structural_facts(io_schema: IOSchema) -> list[str]:
    """io_schema → 그래프 **구조 사실**의 기계 파생 진술 (단일 진실원천, F6~F8).

    formalizer/narrative 프롬프트의 self-loop/다중간선/방향성 prose 규칙을 대체한다 —
    narrative 는 이것을 DATA 로 받아 '서술'하고(규칙 회피가 아니라), QA/faithfulness 는
    narrative ↔ 이 사실을 **기계 비교**로 검증한다. graph_shape 가 핀된 graph 필드만
    대상(없으면 빈 list = 비-graph 문제엔 구조 사실 없음). 인덱싱(F9)은 의미가 아니라
    **형식**이므로 여기 넣지 않는다(render_input_format 의 format 계약 몫).
    """
    facts: list[str] = []
    for f in io_schema.inputs:
        shape = f.graph_shape
        if f.type not in ("weighted_edges", "tree_edges") or shape is None:
            continue
        prefix = f"{f.name}(그래프)"
        direction = "단방향(u→v)" if shape.directed else "양방향(u↔v, 무방향)"
        loop = "자기 간선(self-loop) 가능" if shape.self_loops else "자기 간선 없음"
        multi = (
            "같은 쌍 다중 간선 가능"
            if shape.multi_edges
            else "같은 쌍 다중 간선 없음(단순 그래프)"
        )
        conn = (
            "연결 보장"
            if shape.connectivity == "connected"
            else "연결 비보장(분리 컴포넌트·도달 불가 가능)"
        )
        facts.extend(
            [
                f"{prefix}: {direction} 간선",
                f"{prefix}: {loop}",
                f"{prefix}: {multi}",
                f"{prefix}: {conn}",
            ]
        )
    return facts


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
        return "".join(rng.choice(_ALPHABET) for _ in range(n)), n
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
    vals = " ".join(str(rng.randint(lo, hi)) for _ in range(n))
    return f"{n}\n{vals}", n  # 크기 = 원소 개수 N (참조 바인딩 대상)


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
