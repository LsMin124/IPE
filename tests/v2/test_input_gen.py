"""결정론적 입력 생성 엔진 단위 테스트 (Phase 3 M4 step3 + step3b graph).

generate_inputs(contract, io_schema, seed): 결정론(같은 seed→같은 입력) + tier 범위
존중 + case 수 + int_array 구조 + edge boundary + graph 타입(weighted_edges 연결성/
tree_edges 트리 유효성/grid, disconnected bias).
"""

from __future__ import annotations

from ipe.v1.schema import (
    ConstraintRange,
    EdgeCaseSpec,
    GeneratorContract,
    GraphShape,
    IOFieldSpec,
    IOSchema,
    ScaleFamily,
    SequenceShape,
    StringShape,
)
from ipe.v2.generation.input_gen import (
    _MAX_ELEMENTS,
    _MAX_STRESS_ELEMENTS,
    derive_degenerate_inputs,
    derive_edge_cases,
    derive_generator_contract,
    derive_scale_families,
    describe_io_field,
    format_constraint,
    generate_inputs,
    render_constraints,
    render_input_format,
    seed_from_run_id,
)


def _io_schema(field: IOFieldSpec) -> IOSchema:
    return IOSchema(inputs=(field,), output_type="int", output_format="x")


def _int_array_field() -> IOFieldSpec:
    return IOFieldSpec(
        name="arr",
        type="int_array",
        size_range=ConstraintRange(name="arr", min_value=1, max_value=20),
        value_range=ConstraintRange(name="v", min_value=-5, max_value=5),
    )


# ---------- determinism ----------


def test_generate_inputs_is_deterministic_for_same_seed() -> None:
    schema = _io_schema(_int_array_field())
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="small", case_count=4),)
    )
    a = generate_inputs(contract, schema, seed=42)
    b = generate_inputs(contract, schema, seed=42)
    assert [c.input_text for c in a] == [c.input_text for c in b]


def test_generate_inputs_differs_for_different_seed() -> None:
    schema = _io_schema(_int_array_field())
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="small", case_count=5),)
    )
    a = generate_inputs(contract, schema, seed=1)
    b = generate_inputs(contract, schema, seed=2)
    assert [c.input_text for c in a] != [c.input_text for c in b]


# ---------- case counts ----------


def test_generate_inputs_case_count_and_categories() -> None:
    schema = _io_schema(_int_array_field())
    contract = GeneratorContract(
        scale_families=(
            ScaleFamily(name="small", case_count=3),
            ScaleFamily(name="large", case_count=2),
        ),
        edge_cases=(EdgeCaseSpec(name="single"), EdgeCaseSpec(name="empty")),
    )
    cases = generate_inputs(contract, schema, seed=0)
    assert len(cases) == 7  # 3 + 2 + 2 edge
    assert cases[0].category == "small"
    assert cases[-1].category == "empty"
    assert all(c.expected_output is None for c in cases)  # pending


# ---------- tier bounds ----------


def test_int_scalar_respects_tier_value_bounds() -> None:
    field = IOFieldSpec(
        name="N",
        type="int",
        value_range=ConstraintRange(name="N", min_value=1, max_value=1000000),
    )
    contract = GeneratorContract(
        scale_families=(
            ScaleFamily(
                name="small",
                case_count=10,
                field_bounds=(ConstraintRange(name="N", min_value=1, max_value=5),),
            ),
        )
    )
    for c in generate_inputs(contract, _io_schema(field), seed=7):
        assert 1 <= int(c.input_text) <= 5  # tier 가 값을 좁힘


def test_int_array_structure_and_element_bounds() -> None:
    schema = _io_schema(_int_array_field())
    contract = GeneratorContract(
        scale_families=(
            ScaleFamily(
                name="s",
                case_count=5,
                field_bounds=(ConstraintRange(name="arr", min_value=3, max_value=3),),
            ),
        )
    )
    for c in generate_inputs(contract, schema, seed=3):
        lines = c.input_text.split("\n")
        assert lines[0] == "3"  # N (tier 가 크기 고정)
        vals = lines[1].split()
        assert len(vals) == 3
        assert all(-5 <= int(v) <= 5 for v in vals)  # 원소 value_range


# ---------- edge boundary ----------


def test_edge_empty_array_yields_zero() -> None:
    schema = _io_schema(_int_array_field())
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="empty"),),
    )
    empty = next(
        c for c in generate_inputs(contract, schema, seed=0) if c.category == "empty"
    )
    assert empty.input_text == "0"  # N=0


def test_edge_max_size_picks_upper_bound() -> None:
    field = IOFieldSpec(
        name="arr",
        type="int_array",
        size_range=ConstraintRange(name="arr", min_value=1, max_value=4),
        value_range=ConstraintRange(name="v", min_value=0, max_value=0),
    )
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="max_size"),),
    )
    mx = next(
        c
        for c in generate_inputs(contract, _io_schema(field), seed=0)
        if c.category == "max_size"
    )
    assert mx.input_text.split("\n")[0] == "4"  # 크기 상한


# ---------- sequence_shape sortedness honoring (G1a) ----------


def _shaped_array_field(
    shape: SequenceShape,
    *,
    lo: int = -5,
    hi: int = 5,
    size_lo: int = 1,
    size_hi: int = 20,
) -> IOFieldSpec:
    return IOFieldSpec(
        name="arr",
        type="int_array",
        size_range=ConstraintRange(name="arr", min_value=size_lo, max_value=size_hi),
        value_range=ConstraintRange(name="v", min_value=lo, max_value=hi),
        sequence_shape=shape,
    )


def _array_values(text: str) -> list[int]:
    lines = text.split("\n")
    if lines[0] == "0" or len(lines) < 2:
        return []
    return [int(x) for x in lines[1].split()]


def _fixed_size_contract(n: int, *, cases: int = 5) -> GeneratorContract:
    return GeneratorContract(
        scale_families=(
            ScaleFamily(
                name="s",
                case_count=cases,
                field_bounds=(ConstraintRange(name="arr", min_value=n, max_value=n),),
            ),
        )
    )


def test_sequence_shape_non_decreasing_sorts() -> None:
    field = _shaped_array_field(SequenceShape(sortedness="non_decreasing"))
    for c in generate_inputs(_fixed_size_contract(8), _io_schema(field), seed=3):
        vals = _array_values(c.input_text)
        assert vals == sorted(vals)  # 비내림차 정렬


def test_sequence_shape_strictly_increasing_is_sorted_and_distinct() -> None:
    field = _shaped_array_field(SequenceShape(sortedness="strictly_increasing"))
    for c in generate_inputs(_fixed_size_contract(8), _io_schema(field), seed=3):
        vals = _array_values(c.input_text)
        assert all(vals[i] < vals[i + 1] for i in range(len(vals) - 1))  # 순증가
        assert len(vals) == len(set(vals))  # distinct


def test_sequence_shape_distinct_when_duplicates_disallowed() -> None:
    field = _shaped_array_field(
        SequenceShape(sortedness="unsorted", duplicates_allowed=False)
    )
    for c in generate_inputs(_fixed_size_contract(8), _io_schema(field), seed=3):
        vals = _array_values(c.input_text)
        assert len(vals) == len(set(vals))  # 서로 다른 값


def test_sequence_shape_strictly_increasing_caps_to_range_when_narrow() -> None:
    # 범위 [0,2]=3 distinct 인데 크기 8 요구 → 실현가능성 캡(3개, 정렬 distinct)
    field = _shaped_array_field(
        SequenceShape(sortedness="strictly_increasing"), lo=0, hi=2
    )
    for c in generate_inputs(_fixed_size_contract(8, cases=3), _io_schema(field), seed=3):
        assert _array_values(c.input_text) == [0, 1, 2]


def test_sequence_shape_unsorted_dups_is_byte_identical_to_no_shape() -> None:
    # 핀된 (unsorted, duplicates_allowed=True) 는 미핀과 byte-identical (동일 seed)
    plain = _int_array_field()
    shaped = _shaped_array_field(
        SequenceShape(sortedness="unsorted", duplicates_allowed=True)
    )
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=5),)
    )
    a = generate_inputs(contract, _io_schema(plain), seed=42)
    b = generate_inputs(contract, _io_schema(shaped), seed=42)
    assert [c.input_text for c in a] == [c.input_text for c in b]


# ---------- string_shape alphabet honoring (G2) ----------


def _shaped_string_field(
    shape: StringShape, *, size_lo: int = 5, size_hi: int = 20
) -> IOFieldSpec:
    return IOFieldSpec(
        name="s",
        type="string",
        size_range=ConstraintRange(name="s", min_value=size_lo, max_value=size_hi),
        string_shape=shape,
    )


def _string_cases(field: IOFieldSpec, *, seed: int = 3) -> list[str]:
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=5),))
    return [c.input_text for c in generate_inputs(contract, _io_schema(field), seed=seed)]


def test_string_shape_dna_emits_only_acgt() -> None:
    field = _shaped_string_field(StringShape(alphabet="dna"))
    for text in _string_cases(field):
        assert text  # 비지 않음
        assert set(text) <= set("ACGT")  # DNA 염기만


def test_string_shape_binary_emits_only_01() -> None:
    field = _shaped_string_field(StringShape(alphabet="binary"))
    for text in _string_cases(field):
        assert set(text) <= set("01")


def test_string_shape_uppercase_emits_only_upper() -> None:
    field = _shaped_string_field(StringShape(alphabet="uppercase"))
    for text in _string_cases(field):
        assert set(text) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def test_string_shape_lowercase_byte_identical_to_no_shape() -> None:
    # 핀된 lowercase 는 미핀(현 상수 a-z)과 byte-identical (동일 seed)
    plain = IOFieldSpec(
        name="s",
        type="string",
        size_range=ConstraintRange(name="s", min_value=5, max_value=20),
    )
    shaped = _shaped_string_field(StringShape(alphabet="lowercase"))
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=5),))
    a = generate_inputs(contract, _io_schema(plain), seed=42)
    b = generate_inputs(contract, _io_schema(shaped), seed=42)
    assert [c.input_text for c in a] == [c.input_text for c in b]


# ---------- graph types (step3b) ----------


def _uf_components(n: int, edges: list[tuple[int, int]]) -> tuple[int, bool]:
    """union-find — (컴포넌트 수, 사이클 존재 여부). 정점 1..n."""
    parent = list(range(n + 1))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    has_cycle = False
    comps = n
    for u, v in edges:
        ru, rv = find(u), find(v)
        if ru == rv:
            has_cycle = True
        else:
            parent[ru] = rv
            comps -= 1
    return comps, has_cycle


def _weighted_edges_field(lo: int, hi: int) -> IOFieldSpec:
    return IOFieldSpec(
        name="edges",
        type="weighted_edges",
        size_range=ConstraintRange(name="edges", min_value=lo, max_value=hi),
        value_range=ConstraintRange(name="w", min_value=2, max_value=9),
    )


def _parse_graph(text: str) -> tuple[int, int, list[tuple[int, int, int]]]:
    lines = text.split("\n")
    v, e = (int(x) for x in lines[0].split())
    edges = [tuple(int(x) for x in ln.split()) for ln in lines[1:]]
    assert len(edges) == e
    return v, e, edges  # type: ignore[return-value]


def test_weighted_edges_connected_structure_and_bounds() -> None:
    schema = _io_schema(_weighted_edges_field(6, 6))  # V 고정
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=5),))
    for c in generate_inputs(contract, schema, seed=11):
        v, e, edges = _parse_graph(c.input_text)
        assert v == 6
        assert e >= v - 1  # 연결 backbone + extra
        for u, w_to, wt in edges:
            assert 1 <= u <= v and 1 <= w_to <= v  # 1-indexed
            assert u != w_to  # self-loop 없음
            assert 2 <= wt <= 9  # value_range = 가중치
        comps, _ = _uf_components(v, [(u, t) for u, t, _ in edges])
        assert comps == 1  # 연결 보장


def test_weighted_edges_deterministic_same_seed() -> None:
    schema = _io_schema(_weighted_edges_field(2, 12))
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=4),))
    a = generate_inputs(contract, schema, seed=5)
    b = generate_inputs(contract, schema, seed=5)
    assert [c.input_text for c in a] == [c.input_text for c in b]


def test_weighted_edges_tier_narrows_vertex_count() -> None:
    schema = _io_schema(_weighted_edges_field(1, 100))
    contract = GeneratorContract(
        scale_families=(
            ScaleFamily(
                name="s",
                case_count=3,
                field_bounds=(ConstraintRange(name="edges", min_value=3, max_value=3),),
            ),
        )
    )
    for c in generate_inputs(contract, schema, seed=2):
        v, _, _ = _parse_graph(c.input_text)
        assert v == 3


def test_weighted_edges_min_bias_is_tree_density() -> None:
    schema = _io_schema(_weighted_edges_field(4, 9))
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="min_size"),),
    )
    mn = next(
        c for c in generate_inputs(contract, schema, seed=0) if c.category == "min_size"
    )
    v, e, _ = _parse_graph(mn.input_text)
    assert v == 4  # 크기 하한
    assert e == v - 1  # backbone 만 (최소 밀도)


def test_weighted_edges_empty_bias_single_vertex() -> None:
    schema = _io_schema(_weighted_edges_field(1, 9))
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="empty"),),
    )
    empty = next(
        c for c in generate_inputs(contract, schema, seed=0) if c.category == "empty"
    )
    assert empty.input_text == "1 0"  # 단일 정점, 간선 0


def test_weighted_edges_disconnected_bias_two_components() -> None:
    schema = _io_schema(_weighted_edges_field(6, 6))
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="disconnected"),),
    )
    dc = next(
        c
        for c in generate_inputs(contract, schema, seed=0)
        if c.category == "disconnected"
    )
    v, e, edges = _parse_graph(dc.input_text)
    assert v == 6
    assert e == v - 2  # 두 backbone: (A-1)+(B-1)
    comps, _ = _uf_components(v, [(u, t) for u, t, _ in edges])
    assert comps == 2  # 정확히 두 컴포넌트
    half = (v + 1) // 2
    for u, t, _ in edges:
        same_first = u <= half and t <= half
        same_second = u > half and t > half
        assert same_first or same_second  # 컴포넌트 간 간선 없음


def test_tree_edges_forms_valid_tree() -> None:
    field = IOFieldSpec(
        name="tree",
        type="tree_edges",
        size_range=ConstraintRange(name="tree", min_value=7, max_value=7),
    )
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=3),))
    for c in generate_inputs(contract, _io_schema(field), seed=9):
        lines = c.input_text.split("\n")
        v = int(lines[0])
        assert v == 7
        edges = [tuple(int(x) for x in ln.split()) for ln in lines[1:]]
        assert len(edges) == v - 1
        assert all(len(e) == 2 for e in edges)  # value_range 없음 → 무가중
        comps, has_cycle = _uf_components(v, edges)  # type: ignore[arg-type]
        assert comps == 1 and not has_cycle  # 유효한 트리


def test_tree_edges_with_value_range_adds_weights() -> None:
    field = IOFieldSpec(
        name="tree",
        type="tree_edges",
        size_range=ConstraintRange(name="tree", min_value=5, max_value=5),
        value_range=ConstraintRange(name="w", min_value=1, max_value=3),
    )
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=2),))
    for c in generate_inputs(contract, _io_schema(field), seed=4):
        lines = c.input_text.split("\n")
        for ln in lines[1:]:
            parts = ln.split()
            assert len(parts) == 3  # u v w
            assert 1 <= int(parts[2]) <= 3


def test_grid_matches_int_matrix_canonical() -> None:
    field = IOFieldSpec(
        name="board",
        type="grid",
        size_range=ConstraintRange(name="board", min_value=2, max_value=2),
        value_range=ConstraintRange(name="cell", min_value=0, max_value=1),
    )
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=2),))
    for c in generate_inputs(contract, _io_schema(field), seed=6):
        lines = c.input_text.split("\n")
        assert lines[0] == "2 2"  # R C (int_matrix 와 동일 규약)
        assert len(lines) == 3
        for row in lines[1:]:
            assert all(int(x) in (0, 1) for x in row.split())


# ---------- size caps (80~128MB 패키지·생성 OOM 회귀) ----------


def test_max_scale_graph_input_is_element_capped() -> None:
    """V=200000=7.8MB 입력 실측 회귀 — bias=max 그래프는 스트레스 상한으로 바운드."""
    schema = _io_schema(_weighted_edges_field(2, 50000))  # 본래 대형 그래프
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="max_stress"),),  # bias=max → 상한 크기
    )
    edge = next(
        c for c in generate_inputs(contract, schema, seed=7) if c.category == "max_stress"
    )
    v, e, _ = _parse_graph(edge.input_text)
    assert v <= _MAX_STRESS_ELEMENTS // 2  # 정점 캡 (E ≤ 2V 산식 동일 원칙)
    assert e <= _MAX_STRESS_ELEMENTS  # 총 간선 캡 (max 계열 스트레스 상한)
    assert e > _MAX_ELEMENTS  # 기본 상한을 실제로 초과 활용 (complexity 분리)
    assert len(edge.input_text) < 1_500_000  # 수MB → 수백KB


def test_max_scale_array_input_is_element_capped() -> None:
    """대형 배열(OOM 유발) bias=max 도 N ≤ 스트레스 상한 (기본 상한은 초과 활용)."""
    field = IOFieldSpec(
        name="arr",
        type="int_array",
        size_range=ConstraintRange(name="arr", min_value=1, max_value=100000),
        value_range=ConstraintRange(name="v", min_value=0, max_value=9),
    )
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="max_size"),),
    )
    edge = next(
        c
        for c in generate_inputs(contract, _io_schema(field), seed=3)
        if c.category == "max_size"
    )
    n = int(edge.input_text.split("\n")[0])
    assert n <= _MAX_STRESS_ELEMENTS  # N ≤ 스트레스 상한
    assert n > _MAX_ELEMENTS  # 기본 상한을 실제로 초과 활용 (complexity 분리)


def test_max_scale_matrix_total_elements_capped() -> None:
    """행렬 R*C 원소 총량도 max 계열은 스트레스 상한으로 캡 (R*C 폭주 방지)."""
    field = IOFieldSpec(
        name="m",
        type="int_matrix",
        size_range=ConstraintRange(name="m", min_value=1, max_value=400),
        value_range=ConstraintRange(name="v", min_value=0, max_value=9),
    )
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="max_grid"),),
    )
    edge = next(
        c
        for c in generate_inputs(contract, _io_schema(field), seed=5)
        if c.category == "max_grid"
    )
    r, c = (int(x) for x in edge.input_text.split("\n")[0].split())
    assert r * c <= _MAX_STRESS_ELEMENTS  # 400*400=160000 → 캡 실동작
    assert r * c > _MAX_ELEMENTS  # 스트레스 상한 활용


def test_non_max_cases_keep_default_element_cap() -> None:
    """비-max 케이스(random tier·구조 아키타입)는 기본 상한(1만) 유지 — 패키지/실행 비용 보호."""
    field = IOFieldSpec(
        name="arr",
        type="int_array",
        size_range=ConstraintRange(name="arr", min_value=150000, max_value=200000),
        value_range=ConstraintRange(name="v", min_value=0, max_value=9),
    )
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="large", case_count=3),),
        edge_cases=(EdgeCaseSpec(name="sorted_desc"),),  # 구조 아키타입 (크기 random)
    )
    for case in generate_inputs(contract, _io_schema(field), seed=11):
        assert int(case.input_text.split("\n")[0]) <= _MAX_ELEMENTS  # N ≤ 기본 상한


def test_non_max_graph_archetypes_keep_default_cap() -> None:
    """graph 구조 아키타입(dense 등)도 기본 상한 — 스트레스 상한은 max 계열 전용."""
    schema = _io_schema(_weighted_edges_field(2, 200000))
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="dense"), EdgeCaseSpec(name="cycle_heavy")),
    )
    for case in generate_inputs(contract, schema, seed=4):
        _, e, _ = _parse_graph(case.input_text)
        assert e <= _MAX_ELEMENTS  # V ≤ 기본상한//2 → E ≤ 2V ≤ 기본 상한


def test_stress_cap_cases_remain_deterministic() -> None:
    """스트레스 상한 경로 포함 — 같은 seed → 같은 출력 (결정론 보존)."""
    field = IOFieldSpec(
        name="arr",
        type="int_array",
        size_range=ConstraintRange(name="arr", min_value=1, max_value=200000),
        value_range=ConstraintRange(name="v", min_value=0, max_value=9),
    )
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="max_size"), EdgeCaseSpec(name="max_stress")),
    )
    a = generate_inputs(contract, _io_schema(field), seed=21)
    b = generate_inputs(contract, _io_schema(field), seed=21)
    assert [c.input_text for c in a] == [c.input_text for c in b]


def test_size_cap_preserves_inputs_under_limit() -> None:
    """캡은 상한 초과만 클램프 — 한도 미만 입력은 그대로 (회귀 안전)."""
    schema = _io_schema(_weighted_edges_field(5, 5))
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="max_stress"),),
    )
    edge = next(
        c for c in generate_inputs(contract, schema, seed=1) if c.category == "max_stress"
    )
    v, _, _ = _parse_graph(edge.input_text)
    assert v == 5  # 캡 미만 → 상한 그대로


# ---------- references: 정점/원소 참조 스칼라 (#1 graph trivial/범위밖 해소) ----------


def _graph_and_query_schema(v_lo: int, v_hi: int) -> IOSchema:
    """[weighted_edges grid, int s→grid, int t→grid] — dijkstra 형상."""
    return IOSchema(
        inputs=(
            IOFieldSpec(
                name="grid",
                type="weighted_edges",
                size_range=ConstraintRange(name="grid", min_value=v_lo, max_value=v_hi),
                value_range=ConstraintRange(name="w", min_value=1, max_value=9),
            ),
            IOFieldSpec(name="s", type="int", references="grid"),
            IOFieldSpec(name="t", type="int", references="grid"),
        ),
        output_type="int",
        output_format="x",
    )


def _query_value(text: str, idx: int) -> int:
    """flat 토큰에서 graph(V E + E triples) 뒤의 idx 번째 스칼라."""
    lines = text.split("\n")
    v, e = (int(x) for x in lines[0].split())
    return int(lines[1 + e + idx])


def test_reference_scalar_stays_within_actual_vertex_count() -> None:
    schema = _graph_and_query_schema(5, 5)  # V 고정 5
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=20),))
    for c in generate_inputs(contract, schema, seed=11):
        v = int(c.input_text.split("\n")[0].split()[0])
        s, t = _query_value(c.input_text, 0), _query_value(c.input_text, 1)
        assert 1 <= s <= v and 1 <= t <= v  # 실제 V 이내 (범위밖 RTE 소멸)


def test_reference_scalar_not_trivially_pinned_to_two() -> None:
    """[1,2] trivial 퇴화 회귀 — 큰 V 에서 질의가 2 초과 값을 실제로 가진다."""
    schema = _graph_and_query_schema(50, 50)
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=30),))
    seen = {
        _query_value(c.input_text, 0)
        for c in generate_inputs(contract, schema, seed=3)
    }
    assert max(seen) > 2  # [1,2] 고정이 아니라 전 범위 분산


def test_reference_scalar_valid_even_for_degenerate_single_vertex() -> None:
    """empty bias → V=1 그래프('1 0')라도 s=1 (s>V IndexError '1 0 2 1' 회귀)."""
    schema = _graph_and_query_schema(1, 9)
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="empty"),),
    )
    empty = next(
        c for c in generate_inputs(contract, schema, seed=0) if c.category == "empty"
    )
    lines = empty.input_text.split("\n")
    assert lines[0] == "1 0"  # V=1, E=0
    assert lines[1] == "1" and lines[2] == "1"  # s=t=1 (범위밖 아님)


def test_reference_into_int_array_bound_to_element_count() -> None:
    schema = IOSchema(
        inputs=(
            IOFieldSpec(
                name="arr",
                type="int_array",
                size_range=ConstraintRange(name="arr", min_value=6, max_value=6),
                value_range=ConstraintRange(name="v", min_value=0, max_value=9),
            ),
            IOFieldSpec(name="k", type="int", references="arr"),
        ),
        output_type="int",
        output_format="x",
    )
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=15),))
    for c in generate_inputs(contract, schema, seed=2):
        k = int(c.input_text.split("\n")[-1])
        assert 1 <= k <= 6  # 원소 개수 이내 1-indexed


# ---------- reference_kind: index(위치) vs cardinality(개수) 서술 분기 ----------


def _cardinality_schema(kind: str) -> IOSchema:
    """[int_array fields, int K→fields(reference_kind=kind)] — binary_search '적어도 K개' 형상."""
    return IOSchema(
        inputs=(
            IOFieldSpec(
                name="fields",
                type="int_array",
                size_range=ConstraintRange(name="fields", min_value=4, max_value=4),
                value_range=ConstraintRange(name="v", min_value=1, max_value=9),
            ),
            IOFieldSpec(
                name="K", type="int", references="fields", reference_kind=kind  # type: ignore[arg-type]
            ),
        ),
        output_type="int",
        output_format="x",
    )


def test_cardinality_reference_renders_count_prose() -> None:
    """cardinality 참조는 '개수/수량'·'위치 인덱스가 아니다' 로 서술 — index 의 '가리키는
    번호' 와 갈려 narrative '목표 개수' 서술과 정합(index↔count 모순 해소)."""
    text = render_input_format(_cardinality_schema("cardinality"))
    assert "개수" in text and "위치 인덱스가 아니다" in text
    assert "가리키는" not in text  # 위치 인덱스 단정 안 함
    assert "fields 의 크기 이하" in text  # 데이터 의존 범위는 보존


def test_index_reference_renders_pointer_prose_unchanged() -> None:
    """index(기본) 참조는 현행 '가리키는 1-indexed 번호' 서술 유지 — graph 무회귀."""
    text = render_input_format(_cardinality_schema("index"))
    assert "가리키는" in text and "1-indexed" in text
    assert "위치 인덱스가 아니다" not in text


def test_cardinality_reference_constraint_description() -> None:
    cons_card = {c.name: c for c in render_constraints(_cardinality_schema("cardinality"))}
    cons_idx = {c.name: c for c in render_constraints(_cardinality_schema("index"))}
    assert "개수" in cons_card["K"].description
    assert "번호" not in cons_card["K"].description
    assert "번호" in cons_idx["K"].description  # index 는 현행 유지
    # 숫자 범위·기호는 두 경우 동일 (의미만 갈림, 바인딩 동일)
    assert cons_card["K"].min_value == cons_idx["K"].min_value == 1
    assert cons_card["K"].symbolic_max == cons_idx["K"].symbolic_max == "N"


def test_cardinality_reference_generation_byte_identical_to_index() -> None:
    """reference_kind 는 서술만 가른다 — 생성 입력 바이트는 index 와 완전 동일."""
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=12),))
    idx = [c.input_text for c in generate_inputs(contract, _cardinality_schema("index"), seed=7)]
    card = [
        c.input_text
        for c in generate_inputs(contract, _cardinality_schema("cardinality"), seed=7)
    ]
    assert idx == card  # byte-identical


def test_describe_io_field_marks_reference_kind() -> None:
    card = describe_io_field(
        IOFieldSpec(name="K", type="int", references="fields", reference_kind="cardinality")
    )
    idx = describe_io_field(IOFieldSpec(name="s", type="int", references="grid"))
    assert "개수" in card  # cardinality 마킹
    assert "위치번호" in idx  # index(기본) 마킹


def test_reference_resolves_regardless_of_field_order() -> None:
    """참조 스칼라가 collection 보다 **앞**에 선언돼도 실제 크기에 바인딩."""
    schema = IOSchema(
        inputs=(
            IOFieldSpec(name="s", type="int", references="grid"),
            IOFieldSpec(
                name="grid",
                type="weighted_edges",
                size_range=ConstraintRange(name="grid", min_value=4, max_value=4),
                value_range=ConstraintRange(name="w", min_value=1, max_value=9),
            ),
        ),
        output_type="int",
        output_format="x",
    )
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=12),))
    for c in generate_inputs(contract, schema, seed=8):
        lines = c.input_text.split("\n")
        s = int(lines[0])  # s 가 첫 줄 (선언 순서 유지)
        v = int(lines[1].split()[0])  # grid 헤더
        assert v == 4 and 1 <= s <= 4


def test_reference_generation_is_deterministic() -> None:
    schema = _graph_and_query_schema(2, 12)
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=6),))
    a = generate_inputs(contract, schema, seed=5)
    b = generate_inputs(contract, schema, seed=5)
    assert [c.input_text for c in a] == [c.input_text for c in b]


def test_dangling_reference_defaults_safely() -> None:
    """존재하지 않는 필드 참조(LLM 오타)도 crash 없이 안전값(1)."""
    schema = IOSchema(
        inputs=(IOFieldSpec(name="s", type="int", references="nope"),),
        output_type="int",
        output_format="x",
    )
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=3),))
    for c in generate_inputs(contract, schema, seed=1):
        assert c.input_text == "1"


# ---------- cols_range: int_matrix 열 수 고정 (#2 sort IndexError 해소) ----------


def test_matrix_cols_range_fixes_column_count() -> None:
    """레코드 고정 K 속성 — 행 수는 변해도 열 수는 K 고정 (행별 속성 흔들림 소멸)."""
    field = IOFieldSpec(
        name="records",
        type="int_matrix",
        size_range=ConstraintRange(name="records", min_value=1, max_value=8),
        value_range=ConstraintRange(name="v", min_value=0, max_value=9),
        cols_range=ConstraintRange(name="cols", min_value=3, max_value=3),
    )
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=20),))
    for c in generate_inputs(contract, _io_schema(field), seed=7):
        lines = c.input_text.split("\n")
        r, cols = (int(x) for x in lines[0].split())
        assert cols == 3  # 열 수 고정
        for row in lines[1:]:
            assert len(row.split()) == 3  # 모든 행이 정확히 3 속성


def test_matrix_without_cols_range_unchanged() -> None:
    """cols_range None 이면 현행 동작(열 수도 size_range 에서) — 회귀 안전."""
    field = IOFieldSpec(
        name="m",
        type="int_matrix",
        size_range=ConstraintRange(name="m", min_value=2, max_value=2),
        value_range=ConstraintRange(name="v", min_value=0, max_value=0),
    )
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=4),))
    for c in generate_inputs(contract, _io_schema(field), seed=6):
        lines = c.input_text.split("\n")
        r, cols = (int(x) for x in lines[0].split())
        assert r == 2 and cols == 2  # size_range 가 행·열 모두 (현행)


def test_reference_render_states_relationship() -> None:
    schema = _graph_and_query_schema(1, 100)
    text = render_input_format(schema)
    assert "grid" in text  # 참조 대상 명시
    assert "1-indexed" in text or "1 이상" in text  # 참조 규약 노출


def test_empty_graph_bias_respects_size_min() -> None:
    """V≥2 스키마면 empty 엣지케이스도 '2 0'(V_min) — '1 0'(V=1)은 제약과 모순."""
    schema = _io_schema(_weighted_edges_field(3, 50))  # V≥3
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="empty"),),
    )
    empty = next(
        c for c in generate_inputs(contract, schema, seed=0) if c.category == "empty"
    )
    assert empty.input_text == "3 0"  # V_min=3, 간선 0 (V=1 아님)


def test_empty_tree_bias_respects_size_min() -> None:
    field = IOFieldSpec(
        name="tree",
        type="tree_edges",
        size_range=ConstraintRange(name="tree", min_value=4, max_value=20),
    )
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="empty"),),
    )
    empty = next(
        c
        for c in generate_inputs(contract, _io_schema(field), seed=0)
        if c.category == "empty"
    )
    lines = empty.input_text.split("\n")
    assert int(lines[0]) == 4 and len(lines) == 4  # V_min=4 트리 (V-1=3 간선)


# ---------- render_constraints: 코드 파생 제약 (#1 E/V·V누락 해소) ----------


def test_render_constraints_includes_vertex_count_and_weight() -> None:
    schema = _graph_and_query_schema(2, 100000)
    cons = {c.name: c for c in render_constraints(schema)}
    assert "V" in cons and (cons["V"].min_value, cons["V"].max_value) == (2, 100000)
    assert "w" in cons  # 가중치 누락 안 함


def test_distinct_index_refs_avoids_sample_collision() -> None:
    """``distinct_index_refs=True`` — 같은 collection 을 가리키는 index 참조(s·t)가
    크기 ≥2 에서 서로 다른 값을 갖는다 (전 샘플 s==t 로 핵심 로직 시연 샘플이 없던
    QA blocker, run v2-54d68df4 실측). 기본 False 는 기존 출력 byte-identical."""
    schema = _graph_and_query_schema(2, 3)  # V∈[2,3] — 충돌 확률 최대 구간
    contract = derive_generator_contract(schema)
    for seed in range(20):
        for case in generate_inputs(
            contract, schema, seed=seed, distinct_index_refs=True
        ):
            s = _query_value(case.input_text, 0)
            t = _query_value(case.input_text, 1)
            assert s != t, f"seed={seed} category={case.category}: s==t=={s}"
    # 기본(False) 경로 무회귀 — 같은 seed 에서 flag 유무와 무관하게 동일 출력.
    base = [c.input_text for c in generate_inputs(contract, schema, seed=7)]
    off = [
        c.input_text
        for c in generate_inputs(contract, schema, seed=7, distinct_index_refs=False)
    ]
    assert base == off


def test_render_constraints_emits_edge_count_bound_for_weighted_edges() -> None:
    """weighted_edges 는 E(간선 수) 상한을 렌더 — 생성기 실상한 backbone(V-1)+
    extra(≤V)=2V-1 < 2V 의 코드 파생. E 상한이 constraints 에 없으면 solver 가
    복잡도 설계를 못 해 QA ambiguity blocker (run v2-b4fd4625 실측). tree_edges 는
    E=V-1 이 자명해 별도 행 없음."""
    schema = _graph_and_query_schema(2, 5000)
    cons = {c.name: c for c in render_constraints(schema)}
    assert "E" in cons
    assert cons["E"].min_value == 0
    assert cons["E"].max_value == 10000  # numeric fallback = 2 × V상한
    assert format_constraint(cons["E"]) == "E ∈ [0, 2V]"  # 기호 렌더
    tree = IOSchema(
        inputs=(
            IOFieldSpec(
                name="hierarchy",
                type="tree_edges",
                size_range=ConstraintRange(
                    name="hierarchy", min_value=2, max_value=100
                ),
                value_range=ConstraintRange(name="w", min_value=1, max_value=9),
            ),
        ),
        output_type="int",
        output_format="x",
    )
    assert "E" not in {c.name for c in render_constraints(tree)}


def test_render_constraints_binds_reference_to_collection_max() -> None:
    schema = _graph_and_query_schema(2, 5000)
    cons = {c.name: c for c in render_constraints(schema)}
    # 참조 스칼라 s/t 는 [1, V_max] 로 (리터럴 [1,2] 아님) + 의존 설명
    for q in ("s", "t"):
        assert cons[q].min_value == 1 and cons[q].max_value == 5000
        assert "크기 이하" in cons[q].description


def test_render_constraints_reference_uses_symbolic_max() -> None:
    """참조 스칼라 constraint 는 정적 [1, V상한] 숫자가 아니라 기호 '≤V' 로 렌더 —
    input_format 의 '크기 이하' 서술과 정합(graph QA ambiguity reject 근본원인 해소).
    numeric max_value 는 fallback 으로 보존.
    """
    schema = _graph_and_query_schema(2, 5000)
    cons = {c.name: c for c in render_constraints(schema)}
    for q in ("s", "t"):
        assert cons[q].symbolic_max == "V"  # 컬렉션 크기 기호
        assert cons[q].max_value == 5000  # numeric fallback 보존
        assert format_constraint(cons[q]) == f"{q} ∈ [1, V]"  # 기호 렌더
    assert cons["V"].symbolic_max is None  # 컬렉션 크기는 numeric (데이터 의존 아님)
    assert format_constraint(cons["V"]) == "V ∈ [2, 5000]"


def test_render_constraints_reference_symbolic_zero_indexing() -> None:
    """0-indexed 참조는 '크기 미만' = [0, V-1] 기호 렌더."""
    schema = IOSchema(
        inputs=(
            IOFieldSpec(
                name="grid",
                type="weighted_edges",
                size_range=ConstraintRange(name="grid", min_value=2, max_value=100),
                value_range=ConstraintRange(name="w", min_value=1, max_value=9),
            ),
            IOFieldSpec(name="s", type="int", references="grid"),
        ),
        output_type="int",
        output_format="x",
        indexing=0,
    )
    cons = {c.name: c for c in render_constraints(schema)}
    assert cons["s"].symbolic_max == "V-1"
    assert format_constraint(cons["s"]) == "s ∈ [0, V-1]"


def test_render_constraints_states_fixed_matrix_columns() -> None:
    field = IOFieldSpec(
        name="records",
        type="int_matrix",
        size_range=ConstraintRange(name="records", min_value=1, max_value=2000),
        value_range=ConstraintRange(name="v", min_value=0, max_value=1000),
        cols_range=ConstraintRange(name="cols", min_value=3, max_value=3),
    )
    cons = {c.name: c for c in render_constraints(_io_schema(field))}
    assert "R" in cons and (cons["R"].min_value, cons["R"].max_value) == (1, 2000)
    assert "C" in cons and (cons["C"].min_value, cons["C"].max_value) == (3, 3)


def test_describe_io_field_surfaces_reference_and_cols() -> None:
    ref = describe_io_field(IOFieldSpec(name="s", type="int", references="grid"))
    assert "→refs grid" in ref and "1..|grid|" in ref  # 참조 관계 노출
    mtx = describe_io_field(
        IOFieldSpec(
            name="m",
            type="int_matrix",
            size_range=ConstraintRange(name="m", min_value=1, max_value=9),
            cols_range=ConstraintRange(name="c", min_value=2, max_value=2),
        )
    )
    assert "size[1..9]" in mtx and "cols[2..2]" in mtx  # 행수+고정열수 분리 노출


# ---------- seed helper ----------


def test_seed_from_run_id_is_stable_and_distinct() -> None:
    assert seed_from_run_id("run-x") == seed_from_run_id("run-x")
    assert seed_from_run_id("run-x") != seed_from_run_id("run-y")


# ---------- canonical input_format 렌더 (step6) ----------


def test_render_weighted_edges_states_canonical_rules() -> None:
    schema = _io_schema(_weighted_edges_field(1, 100))
    text = render_input_format(schema)
    assert "V E" in text  # 헤더 규약
    assert "u v w" in text  # 간선 줄 규약
    assert "1-indexed" in text  # 인덱싱 — ratio 0.0 의 유력 원인이던 항목
    assert "연결" in text  # 연결 비보장 명시


def test_render_tree_edges_weighted_and_unweighted() -> None:
    base = IOFieldSpec(
        name="tree",
        type="tree_edges",
        size_range=ConstraintRange(name="tree", min_value=1, max_value=9),
    )
    unweighted = render_input_format(_io_schema(base))
    assert "u v" in unweighted and "트리" in unweighted
    weighted = render_input_format(
        _io_schema(
            base.model_copy(
                update={
                    "value_range": ConstraintRange(name="w", min_value=1, max_value=5)
                }
            )
        )
    )
    assert "u v w" in weighted


def test_render_int_array_states_count_header() -> None:
    text = render_input_format(_io_schema(_int_array_field()))
    assert "N" in text and "공백" in text  # 'N 줄 + 공백구분' 규약


def test_render_multi_field_preserves_order() -> None:
    schema = IOSchema(
        inputs=(
            IOFieldSpec(name="K", type="int"),
            IOFieldSpec(name="arr", type="int_array"),
        ),
        output_type="int",
        output_format="x",
    )
    text = render_input_format(schema)
    assert text.index("K") < text.index("arr")  # io_schema 순서 유지
    assert "1)" in text and "2)" in text  # 필드 순번 명시


# ---------- int_array N=0 절 / sortedness 단일소스 (Task B — sequence write-side) ----------


def test_render_int_array_omits_empty_clause_when_min_positive() -> None:
    # min≥1 → 'N=0' 절 없음 (render_constraints 의 N≥min 과 정합). loop_accumulate(3/3)·
    # binary_search(3/3)·lis(3/3) 가 'N=0↔constraints' 모순으로 reject 되던 결함의 해소.
    text = render_input_format(_io_schema(_int_array_field()))  # min_value=1
    assert "N=0" not in text
    # size_range=None (방어 기본 _DEFAULT_SIZE min=1) 도 절 없음 — 명시 확인.
    none_size = render_input_format(_io_schema(IOFieldSpec(name="a", type="int_array")))
    assert "N=0" not in none_size


def test_render_int_array_strictly_increasing_uses_math_form() -> None:
    # 순증가도 수식형(a[i] < a[i+1])으로 — structural_facts 와 동일 라벨(단일소스) 확인.
    field = _int_array_field().model_copy(
        update={"sequence_shape": SequenceShape(sortedness="strictly_increasing")}
    )
    text = render_input_format(_io_schema(field))
    assert "a[i] < a[i+1]" in text
    assert "중복 없음" in text


def test_render_int_array_states_empty_clause_when_min_zero() -> None:
    # size_range 가 N=0 을 실제 허용(min==0)할 때만 빈 수열 절 방출 — size_range.min 단일소스.
    field = _int_array_field().model_copy(
        update={"size_range": ConstraintRange(name="arr", min_value=0, max_value=20)}
    )
    text = render_input_format(_io_schema(field))
    assert "N=0" in text  # 빈 수열 허용 → 절 명시 (constraints N∈[0,20] 과 정합)


def test_render_int_array_sortedness_is_unambiguous() -> None:
    # non_decreasing 은 수식형(a[i] ≤ a[i+1])으로만 표기 — 평문 '오름차순' 병기는 순증가로
    # 읽혀 '중복 값 가능' 과 충돌해 QA ambiguity reject 됐다(binary_search 실측).
    field = _int_array_field().model_copy(
        update={
            "sequence_shape": SequenceShape(
                sortedness="non_decreasing", duplicates_allowed=True
            )
        }
    )
    text = render_input_format(_io_schema(field))
    assert "오름차순" not in text
    assert "a[i] ≤ a[i+1]" in text
    assert "중복 값 가능" in text


def test_sortedness_label_single_sourced_with_structural_facts() -> None:
    # format prose(render_input_format)와 narrative DATA(SequenceBackbone.structural_facts)가
    # 같은 SORTEDNESS_LABEL 을 공유 → 한 곳에서 닫힌 모호성이 다른 곳에서 되살아날 수 없다.
    from ipe.v2.backbone import SequenceBackbone

    field = _int_array_field().model_copy(
        update={"sequence_shape": SequenceShape(sortedness="non_decreasing")}
    )
    fmt = render_input_format(_io_schema(field))
    facts = " | ".join(SequenceBackbone().structural_facts(_io_schema(field)))
    assert "비내림차순 정렬(a[i] ≤ a[i+1])" in fmt
    assert "비내림차순 정렬(a[i] ≤ a[i+1])" in facts


# ---------- GraphShape: 구조 사실 IR 필드 (Phase 1 F6~F8) ----------


def _shaped_edges(shape: GraphShape, lo: int = 5, hi: int = 5) -> IOFieldSpec:
    return _weighted_edges_field(lo, hi).model_copy(update={"graph_shape": shape})


def test_graph_shape_default_is_byte_identical_to_none() -> None:
    """graph_shape=GraphShape(기본값)은 graph_shape=None 과 byte-identical (현 상수=기본값).

    Phase 1 무위험 핵심 — formalizer 가 변주하기 전까지 생성 바이트 불변.
    """
    schema_none = _io_schema(_weighted_edges_field(2, 12))
    schema_default = _io_schema(_shaped_edges(GraphShape(directed=True), 2, 12))
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=6),),
        edge_cases=(EdgeCaseSpec(name="disconnected"), EdgeCaseSpec(name="min")),
    )
    a = generate_inputs(contract, schema_none, seed=5)
    b = generate_inputs(contract, schema_default, seed=5)
    assert [c.input_text for c in a] == [c.input_text for c in b]


def test_graph_shape_self_loops_allows_self_edges() -> None:
    """self_loops=True → 자기 간선(u==t) 출현 가능 (현 상수 False 의 회피를 끈다)."""
    field = _shaped_edges(GraphShape(directed=True, self_loops=True), 3, 3)
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=60),))
    has_self_loop = any(
        u == t
        for c in generate_inputs(contract, _io_schema(field), seed=1)
        for u, t, _ in _parse_graph(c.input_text)[2]
    )
    assert has_self_loop


def test_graph_shape_self_loops_false_never_self_edges() -> None:
    """self_loops=False(기본) → 자기 간선 절대 없음 (회귀 가드)."""
    field = _shaped_edges(GraphShape(directed=True, self_loops=False), 3, 3)
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=60),))
    for c in generate_inputs(contract, _io_schema(field), seed=1):
        for u, t, _ in _parse_graph(c.input_text)[2]:
            assert u != t


def test_graph_shape_no_multi_edges_yields_simple_graph() -> None:
    """multi_edges=False → 같은 (u,t) 중복 간선 없음 (단순 그래프)."""
    field = _shaped_edges(GraphShape(directed=True, multi_edges=False), 5, 5)
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=40),))
    for c in generate_inputs(contract, _io_schema(field), seed=2):
        pairs = [(u, t) for u, t, _ in _parse_graph(c.input_text)[2]]
        assert len(pairs) == len(set(pairs))  # directed 순서쌍 유일


def test_graph_shape_connected_overrides_disconnected_bias() -> None:
    """connectivity=connected → disconnected 엣지케이스도 단일 컴포넌트(구조 사실 우선)."""
    field = _shaped_edges(GraphShape(directed=True, connectivity="connected"), 6, 6)
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="disconnected"),),
    )
    dc = next(
        c
        for c in generate_inputs(contract, _io_schema(field), seed=0)
        if c.category == "disconnected"
    )
    v, _, edges = _parse_graph(dc.input_text)
    comps, _ = _uf_components(v, [(u, t) for u, t, _ in edges])
    assert comps == 1


# ---------- indexing: 0-indexed 투영 (Phase 1 F9) ----------


def test_indexing_zero_produces_zero_based_vertices() -> None:
    schema = IOSchema(
        inputs=(_weighted_edges_field(5, 5),),
        output_type="int",
        output_format="x",
        indexing=0,
    )
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=10),))
    saw_zero = False
    for c in generate_inputs(contract, schema, seed=3):
        v, _, edges = _parse_graph(c.input_text)
        assert v == 5
        for u, t, _ in edges:
            assert 0 <= u <= v - 1 and 0 <= t <= v - 1  # 0..V-1
            if u == 0 or t == 0:
                saw_zero = True
    assert saw_zero  # 0-indexed 하한 정점 실제 등장


def test_indexing_zero_reference_is_zero_based() -> None:
    schema = IOSchema(
        inputs=(
            IOFieldSpec(
                name="grid",
                type="weighted_edges",
                size_range=ConstraintRange(name="grid", min_value=5, max_value=5),
                value_range=ConstraintRange(name="w", min_value=1, max_value=9),
            ),
            IOFieldSpec(name="s", type="int", references="grid"),
        ),
        output_type="int",
        output_format="x",
        indexing=0,
    )
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=20),))
    seen = set()
    for c in generate_inputs(contract, schema, seed=11):
        s = int(c.input_text.split("\n")[-1])
        assert 0 <= s <= 4  # [0, V-1]
        seen.add(s)
    assert 0 in seen  # 0-indexed 하한 도달


def test_indexing_one_is_byte_identical_to_default() -> None:
    """indexing=1 명시 == indexing 생략(기본 1) — 회귀 가드."""
    field = _weighted_edges_field(2, 12)
    default = IOSchema(inputs=(field,), output_type="int", output_format="x")
    explicit = IOSchema(
        inputs=(field,), output_type="int", output_format="x", indexing=1
    )
    contract = GeneratorContract(scale_families=(ScaleFamily(name="s", case_count=6),))
    a = generate_inputs(contract, default, seed=5)
    b = generate_inputs(contract, explicit, seed=5)
    assert [c.input_text for c in a] == [c.input_text for c in b]


def test_render_input_format_reflects_graph_shape_and_indexing() -> None:
    field = _shaped_edges(GraphShape(directed=False, self_loops=True), 2, 10)
    schema = IOSchema(
        inputs=(field,), output_type="int", output_format="x", indexing=0
    )
    text = render_input_format(schema)
    assert "0-indexed" in text and "0..V-1" in text
    assert "양방향" in text  # directed=False 투영
    assert "self-loop 가능" in text


def test_render_constraints_reference_respects_zero_indexing() -> None:
    schema = IOSchema(
        inputs=(
            IOFieldSpec(
                name="grid",
                type="weighted_edges",
                size_range=ConstraintRange(name="grid", min_value=2, max_value=100),
                value_range=ConstraintRange(name="w", min_value=1, max_value=9),
            ),
            IOFieldSpec(name="s", type="int", references="grid"),
        ),
        output_type="int",
        output_format="x",
        indexing=0,
    )
    cons = {c.name: c for c in render_constraints(schema)}
    assert cons["s"].min_value == 0 and cons["s"].max_value == 99  # [0, V-1]
    assert "미만" in cons["s"].description


# ---------- GeneratorContract 순수 투영 (Phase 3 — derive_*) ----------


def _derive_edges_field(
    *, lo: int = 2, hi: int = 1000, shape: GraphShape | None = None
) -> IOFieldSpec:
    return IOFieldSpec(
        name="edges",
        type="weighted_edges",
        size_range=ConstraintRange(name="V", min_value=lo, max_value=hi),
        value_range=ConstraintRange(name="w", min_value=1, max_value=9),
        graph_shape=shape,
    )


def test_derive_scalar_schema_single_nominal_family() -> None:
    """sized 필드 없는 스칼라 schema — 좁힐 규모가 없어 단일 nominal family, edge 없음."""
    schema = _io_schema(
        IOFieldSpec(
            name="N",
            type="int",
            value_range=ConstraintRange(name="N", min_value=1, max_value=100),
        )
    )
    families = derive_scale_families(schema)
    assert [f.name for f in families] == ["nominal"]
    assert families[0].field_bounds == ()  # 좁힐 규모 없음
    assert derive_edge_cases(schema) == ()  # 스칼라엔 실현 가능 퇴화 없음


def test_derive_graph_schema_tiers_and_edges() -> None:
    """weighted_edges(shape=None 레거시, 대규모) — small/large tier + 4 실현가능 edge
    + adversarial 아키타입(max_stress·구조·가중치 — 상용 저지 케이스 강도)."""
    schema = _io_schema(_derive_edges_field())
    assert {f.name for f in derive_scale_families(schema)} == {"small", "large"}
    assert {e.name for e in derive_edge_cases(schema)} == {
        "min_size",
        "max_size",
        "empty",
        "disconnected",
        "max_stress",
        "path_chain",
        "star",
        "dense",
        "cycle_heavy",
        "duplicate_edges",
        "equal_weights",
        "extreme_weights",
    }


def test_derive_int_array_min_one_excludes_empty() -> None:
    """크기 하한 1 배열 — empty(크기 0)는 제약(크기≥1) 위반이라 미방출(realizability gate).
    미핀 대규모 배열이라 sequence adversarial 아키타입은 전부 방출."""
    schema = _io_schema(
        IOFieldSpec(
            name="arr",
            type="int_array",
            size_range=ConstraintRange(name="arr", min_value=1, max_value=50),
        )
    )
    assert {e.name for e in derive_edge_cases(schema)} == {
        "min_size",
        "max_size",
        "max_stress",
        "sorted_asc",
        "sorted_desc",
        "alternating",
        "all_equal",
        "extreme_values",
        "single_element",
    }


def test_derive_int_array_min_zero_includes_empty() -> None:
    """크기 하한 0 허용 배열 — empty(크기 0)가 제약과 정합이라 방출."""
    schema = _io_schema(
        IOFieldSpec(
            name="arr",
            type="int_array",
            size_range=ConstraintRange(name="arr", min_value=0, max_value=50),
        )
    )
    assert "empty" in {e.name for e in derive_edge_cases(schema)}


def test_derive_tree_edges_no_disconnected() -> None:
    """tree_edges 는 정의상 연결 → disconnected 미방출. size_min>=2 면 최소 트리도 간선
    1+개라 'empty'(간선없는) 거짓 → 미방출 (카테고리명↔입력 정합, F18 류 불일치 방지)."""
    schema = _io_schema(
        IOFieldSpec(
            name="tree",
            type="tree_edges",
            size_range=ConstraintRange(name="V", min_value=2, max_value=100),
        )
    )
    names = {e.name for e in derive_edge_cases(schema)}
    assert "disconnected" not in names
    # V_min=2 트리는 간선 1개 — empty 거짓. 트리엔 chain/star 만(사이클·다중간선·밀도
    # 불가), 무가중(value_range=None)이라 가중치 아키타입도 미방출.
    assert names == {"min_size", "max_size", "max_stress", "path_chain", "star"}


def test_derive_tree_edges_empty_only_when_single_vertex() -> None:
    """tree_edges size_min<=1 — empty 가 단일 정점('1', 간선 0)으로 genuine → 방출·실현."""
    schema = _io_schema(
        IOFieldSpec(
            name="tree",
            type="tree_edges",
            size_range=ConstraintRange(name="V", min_value=1, max_value=100),
        )
    )
    contract = derive_generator_contract(schema)
    by_cat = {c.category: c for c in generate_inputs(contract, schema, seed=3)}
    assert by_cat["empty"].input_text == "1"  # 단일 정점·간선 0 (genuine empty)


def test_derive_connected_graph_excludes_disconnected() -> None:
    """connectivity='connected' 핀 그래프 — 직렬화기가 단일 컴포넌트 강제라 disconnected 미방출."""
    shape = GraphShape(directed=True, connectivity="connected")
    schema = _io_schema(_derive_edges_field(shape=shape))
    assert "disconnected" not in {e.name for e in derive_edge_cases(schema)}


def test_derive_scale_tiers_within_declared_range() -> None:
    """tier field_bounds 는 io_schema size_range 경계 안 + log 중앙 분할(small ≤ large)."""
    schema = _io_schema(_derive_edges_field(lo=2, hi=1000))
    families = {f.name: f for f in derive_scale_families(schema)}
    small = families["small"].field_bounds[0]
    large = families["large"].field_bounds[0]
    assert small.min_value == 2 and large.max_value == 1000  # 선언 범위 경계 보존
    assert 2 <= small.max_value <= large.max_value <= 1000  # log 중앙 분할·범위 내


def test_derive_contract_round_trips_to_realizable_inputs() -> None:
    """파생 계약 round-trip: empty edge='V_min 0', disconnected edge=V-2 edges."""
    schema = _io_schema(_derive_edges_field(lo=2, hi=20))
    contract = derive_generator_contract(schema)
    by_cat = {c.category: c for c in generate_inputs(contract, schema, seed=7)}
    # empty = 정점 하한·간선 0 (size_range.min 존중 → 제약 모순 없음)
    assert by_cat["empty"].input_text == "2 0"
    # disconnected = 두 backbone(각 component-1) = V-2 간선 (분리 실현)
    v, e = (int(x) for x in by_cat["disconnected"].input_text.splitlines()[0].split())
    assert e == v - 2


def test_derive_empty_suppressed_when_any_sized_field_unsafe() -> None:
    """graph(empty-safe) + array(min>=1, empty-unsafe) 혼합 — empty bias 가 전 필드 동시
    적용이라 array 가 크기 0 으로 제약 위반. 모든 sized 가 안전할 때만 empty 방출 → 미방출."""
    schema = IOSchema(
        inputs=(
            _derive_edges_field(lo=2, hi=20),
            IOFieldSpec(
                name="arr",
                type="int_array",
                size_range=ConstraintRange(name="arr", min_value=1, max_value=10),
            ),
        ),
        output_type="int",
        output_format="x",
    )
    names = {e.name for e in derive_edge_cases(schema)}
    assert "empty" not in names  # array(min=1) 가 empty 를 억제
    assert {"min_size", "max_size", "disconnected"} <= names  # 나머지는 여전히 방출


# ---------- derive_degenerate_inputs (Phase 5a — reconcile Tier B 퇴화 probe) ----------


def test_derive_degenerate_inputs_min_and_unreachable_for_graph() -> None:
    # dijkstra 형상(분리가능 graph) → min(경계) + unreachable(분리) 둘 다 실현
    schema = _graph_and_query_schema(2, 10)
    degens = derive_degenerate_inputs(schema)
    assert [name for name, _text, _rat in degens] == ["min", "unreachable"]
    assert all(text for _n, text, _r in degens)  # 비지 않은 직렬화
    assert all(rat for _n, _t, rat in degens)  # 사람 설명


def test_derive_degenerate_inputs_min_folds_source_equals_target() -> None:
    # min bias → 질의 참조 스칼라가 모두 하한으로 수렴 → s==t (source_equals_target 겸함)
    schema = _graph_and_query_schema(2, 10)
    min_text = derive_degenerate_inputs(schema)[0][1]
    assert _query_value(min_text, 0) == _query_value(min_text, 1)  # s == t


def test_derive_degenerate_inputs_unreachable_separates_query_vertices() -> None:
    # unreachable 입력은 두 컴포넌트 분리 그래프 (도달 불가 의미를 probe)
    schema = _graph_and_query_schema(6, 6)
    unreachable_text = derive_degenerate_inputs(schema)[1][1]
    v, e = (int(x) for x in unreachable_text.split("\n")[0].split())
    assert e == v - 2  # 두 컴포넌트 backbone (각 절반-1 간선 = V-2)


def test_derive_degenerate_inputs_min_only_for_connected_graph() -> None:
    # connectivity=connected → 분리 미실현 → min 만
    base = _graph_and_query_schema(2, 10)
    grid = base.inputs[0].model_copy(
        update={"graph_shape": GraphShape(directed=True, connectivity="connected")}
    )
    schema = base.model_copy(update={"inputs": (grid, *base.inputs[1:])})
    assert [n for n, _t, _r in derive_degenerate_inputs(schema)] == ["min"]


def test_derive_degenerate_inputs_empty_for_scalar_only_schema() -> None:
    # sized 필드 없음(스칼라 only) → probe 할 퇴화 없음
    schema = IOSchema(
        inputs=(
            IOFieldSpec(
                name="x",
                type="int",
                value_range=ConstraintRange(name="x", min_value=1, max_value=9),
            ),
        ),
        output_type="int",
        output_format="y",
    )
    assert derive_degenerate_inputs(schema) == ()


def test_derive_degenerate_inputs_deterministic() -> None:
    # 고정 seed — reconcile 가 diff 한 입력 == edge_filler 가 채우는 입력 보장
    schema = _graph_and_query_schema(3, 12)
    assert derive_degenerate_inputs(schema) == derive_degenerate_inputs(schema)


# ---------- adversarial 아키타입 (상용 저지 케이스 강도) ----------


def _edge_case_text(schema: IOSchema, name: str, *, seed: int = 0) -> str:
    """edge case 하나만 담은 contract 로 해당 카테고리 입력 텍스트를 얻는다."""
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name=name),),
    )
    return next(
        c.input_text
        for c in generate_inputs(contract, schema, seed=seed)
        if c.category == name
    )


def _plain_array(n: int) -> IOFieldSpec:
    """크기 n 고정, 값 [-5,5], shape 미핀 int_array."""
    return IOFieldSpec(
        name="arr",
        type="int_array",
        size_range=ConstraintRange(name="arr", min_value=n, max_value=n),
        value_range=ConstraintRange(name="v", min_value=-5, max_value=5),
    )


def test_path_chain_is_linear_chain() -> None:
    schema = _io_schema(_weighted_edges_field(12, 12))
    v, e, edges = _parse_graph(_edge_case_text(schema, "path_chain"))
    assert v == 12 and e == v - 1
    assert [(u, t) for u, t, _ in edges] == [(i, i + 1) for i in range(1, 12)]  # 사슬
    assert all(2 <= w <= 9 for _, _, w in edges)  # value_range 존중


def test_star_concentrates_on_hub() -> None:
    schema = _io_schema(_weighted_edges_field(12, 12))
    v, e, edges = _parse_graph(_edge_case_text(schema, "star"))
    assert v == 12 and e == v - 1
    assert all(u == 1 for u, _, _ in edges)  # 허브(1-indexed base) 집중
    assert sorted(t for _, t, _ in edges) == list(range(2, 13))  # 나머지 전 정점


def test_dense_hits_edge_count_upper() -> None:
    schema = _io_schema(_weighted_edges_field(12, 12))
    v, e, edges = _parse_graph(_edge_case_text(schema, "dense"))
    assert e == 2 * v - 1  # backbone(V-1) + extra(V) — 간선 수 상한 근접
    comps, _ = _uf_components(v, [(u, t) for u, t, _ in edges])
    assert comps == 1  # 여전히 연결


def test_cycle_heavy_is_chain_plus_cycles() -> None:
    schema = _io_schema(_weighted_edges_field(12, 12))
    v, e, edges = _parse_graph(_edge_case_text(schema, "cycle_heavy"))
    pairs = [(u, t) for u, t, _ in edges]
    assert pairs[: v - 1] == [(i, i + 1) for i in range(1, v)]  # 사슬 backbone
    assert e == 2 * v - 1  # 추가 간선 v 개 — 간선당 사이클 1+
    comps, has_cycle = _uf_components(v, pairs)
    assert comps == 1 and has_cycle


def test_duplicate_edges_repeats_pairs_when_multi_allowed() -> None:
    schema = _io_schema(_weighted_edges_field(12, 12))  # shape=None → 다중간선 허용(현 상수)
    v, e, edges = _parse_graph(_edge_case_text(schema, "duplicate_edges"))
    pairs = [(u, t) for u, t, _ in edges]
    assert e == 2 * v - 1  # backbone + v 개 중복 재방출
    assert len(set(pairs)) < len(pairs)  # 같은 쌍 반복 실재


def test_duplicate_edges_bias_respects_simple_graph_pin() -> None:
    """multi_edges=False 핀 — 아키타입 bias 가 와도 단순 그래프 유지 (핀 승리, 방어)."""
    field = _shaped_edges(GraphShape(directed=True, multi_edges=False), 12, 12)
    _, _, edges = _parse_graph(_edge_case_text(_io_schema(field), "duplicate_edges"))
    pairs = [(u, t) for u, t, _ in edges]
    assert len(pairs) == len(set(pairs))  # 중복 없음


def test_equal_weights_all_weights_identical() -> None:
    schema = _io_schema(_weighted_edges_field(12, 12))
    _, _, edges = _parse_graph(_edge_case_text(schema, "equal_weights"))
    weights = {w for _, _, w in edges}
    assert len(weights) == 1  # 전 간선 동일 가중치 (tie 스트레스)
    assert all(2 <= w <= 9 for w in weights)


def test_extreme_weights_mixes_bounds_only() -> None:
    schema = _io_schema(_weighted_edges_field(30, 30))
    _, _, edges = _parse_graph(_edge_case_text(schema, "extreme_weights", seed=1))
    weights = {w for _, _, w in edges}
    assert weights == {2, 9}  # 하한/상한만 혼재 (seed 고정 → 결정론)


def test_tree_path_chain_and_star_shapes() -> None:
    field = IOFieldSpec(
        name="tree",
        type="tree_edges",
        size_range=ConstraintRange(name="tree", min_value=10, max_value=10),
    )
    chain_lines = _edge_case_text(_io_schema(field), "path_chain").split("\n")
    assert chain_lines[0] == "10"
    chain = [tuple(int(x) for x in ln.split()) for ln in chain_lines[1:]]
    assert chain == [(i, i + 1) for i in range(1, 10)]  # 사슬 트리 (깊이 최대)
    star_lines = _edge_case_text(_io_schema(field), "star").split("\n")
    star = [tuple(int(x) for x in ln.split()) for ln in star_lines[1:]]
    assert len(star) == 9 and all(u == 1 for u, _ in star)  # 스타 트리 (허브 집중)


def test_sorted_asc_bias_emits_ascending() -> None:
    vals = _array_values(_edge_case_text(_io_schema(_plain_array(10)), "sorted_asc", seed=2))
    assert len(vals) == 10 and vals == sorted(vals)
    assert all(-5 <= v <= 5 for v in vals)


def test_sorted_desc_bias_emits_descending() -> None:
    vals = _array_values(_edge_case_text(_io_schema(_plain_array(10)), "sorted_desc", seed=2))
    assert len(vals) == 10 and vals == sorted(vals, reverse=True)


def test_all_equal_bias_emits_identical_values() -> None:
    vals = _array_values(_edge_case_text(_io_schema(_plain_array(10)), "all_equal", seed=2))
    assert len(vals) == 10 and len(set(vals)) == 1
    assert -5 <= vals[0] <= 5


def test_alternating_bias_zigzags() -> None:
    from ipe.v2.generation.input_gen import _zigzag

    vals = _array_values(_edge_case_text(_io_schema(_plain_array(11)), "alternating", seed=3))
    assert len(vals) == 11
    assert vals == _zigzag(sorted(vals))  # 저-고 교대 재배치 (다중집합 불변)
    assert vals[0] == min(vals) and vals[1] == max(vals)


def test_single_element_bias_yields_n_one() -> None:
    field = IOFieldSpec(
        name="arr",
        type="int_array",
        size_range=ConstraintRange(name="arr", min_value=1, max_value=50),
        value_range=ConstraintRange(name="v", min_value=-5, max_value=5),
    )
    text = _edge_case_text(_io_schema(field), "single_element", seed=4)
    lines = text.split("\n")
    assert lines[0] == "1" and len(lines[1].split()) == 1  # N=1


def test_extreme_values_bias_uses_bounds_only() -> None:
    vals = _array_values(
        _edge_case_text(_io_schema(_plain_array(20)), "extreme_values", seed=5)
    )
    assert set(vals) == {-5, 5}  # 하한/상한 혼재 (seed 고정 → 결정론)


def test_sorted_desc_bias_defers_to_sort_pin() -> None:
    """non_decreasing 핀 배열에 sorted_desc bias — 핀 승리(오름차순 유지, 위반 불가)."""
    field = _shaped_array_field(
        SequenceShape(sortedness="non_decreasing"), size_lo=10, size_hi=10
    )
    vals = _array_values(_edge_case_text(_io_schema(field), "sorted_desc", seed=4))
    assert len(vals) == 10 and vals == sorted(vals)


def test_all_equal_bias_defers_to_distinct_pin() -> None:
    """strictly_increasing 핀 배열에 all_equal bias — 핀 승리(순증가 distinct 유지)."""
    field = _shaped_array_field(
        SequenceShape(sortedness="strictly_increasing"), size_lo=10, size_hi=10
    )
    vals = _array_values(_edge_case_text(_io_schema(field), "all_equal", seed=4))
    assert all(vals[i] < vals[i + 1] for i in range(len(vals) - 1))


def test_all_same_char_bias_single_char_within_alphabet() -> None:
    field = _shaped_string_field(StringShape(alphabet="dna"), size_lo=12, size_hi=12)
    text = _edge_case_text(_io_schema(field), "all_same_char")
    assert len(text) == 12 and len(set(text)) == 1
    assert set(text) <= set("ACGT")  # alphabet 핀 존중


def test_periodic_bias_repeats_short_period() -> None:
    field = _shaped_string_field(StringShape(alphabet="lowercase"), size_lo=12, size_hi=12)
    text = _edge_case_text(_io_schema(field), "periodic")
    assert len(text) == 12
    assert any(
        all(text[i] == text[i % p] for i in range(len(text))) for p in (2, 3)
    )  # 주기 2~3 반복


def test_max_stress_guarantees_two_upper_bound_cases() -> None:
    """max_size + max_stress — 진짜 상한(V=hi·최대 밀도) 케이스 2개 보장 (naive TLE 유도)."""
    schema = _io_schema(_weighted_edges_field(2, 60))
    contract = derive_generator_contract(schema)
    cases = generate_inputs(contract, schema, seed=9)
    upper = [c for c in cases if c.category in ("max_size", "max_stress")]
    assert len(upper) == 2
    for c in upper:
        v, e, _ = _parse_graph(c.input_text)
        assert v == 60  # size 상한 실타격
        assert e == 2 * v - 1  # 최대 밀도
    assert upper[0].input_text != upper[1].input_text  # 서로 다른 rng 스트림(중복 케이스 아님)


def test_derive_small_schema_keeps_legacy_edge_set() -> None:
    """size 상한 < _ADVERSARIAL_MIN_SIZE — 아키타입 미방출(기존 소형 suite 정책 보존)."""
    schema = _io_schema(_weighted_edges_field(3, 8))
    assert {e.name for e in derive_edge_cases(schema)} == {
        "min_size",
        "max_size",
        "empty",
        "disconnected",
    }


def test_derive_pinned_sequence_gates_conflicting_archetypes() -> None:
    """strictly_increasing 핀 — 모순/중복 아키타입 미방출(카테고리명↔입력 정합, F18 동형)."""
    field = _shaped_array_field(
        SequenceShape(sortedness="strictly_increasing"), size_lo=1, size_hi=100
    )
    names = {e.name for e in derive_edge_cases(_io_schema(field))}
    for banned in ("sorted_asc", "sorted_desc", "alternating", "all_equal", "extreme_values"):
        assert banned not in names
    assert {"max_stress", "single_element"} <= names  # 핀과 무관한 아키타입은 방출


def test_derive_non_decreasing_allows_tie_archetypes_only() -> None:
    """non_decreasing(중복 허용) 핀 — 재배치류 미방출, tie/극단값 아키타입은 방출."""
    field = _shaped_array_field(
        SequenceShape(sortedness="non_decreasing"), size_lo=1, size_hi=100
    )
    names = {e.name for e in derive_edge_cases(_io_schema(field))}
    for banned in ("sorted_asc", "sorted_desc", "alternating"):
        assert banned not in names
    assert {"all_equal", "extreme_values"} <= names


def test_derive_simple_graph_pin_gates_duplicate_edges() -> None:
    """multi_edges=False 핀 — duplicate_edges 미방출, 나머지 구조 아키타입은 방출."""
    shape = GraphShape(directed=True, multi_edges=False)
    schema = _io_schema(_shaped_edges(shape, 2, 100))
    names = {e.name for e in derive_edge_cases(schema)}
    assert "duplicate_edges" not in names
    assert {"path_chain", "star", "dense", "cycle_heavy"} <= names


def test_derive_string_schema_emits_string_archetypes() -> None:
    field = _shaped_string_field(StringShape(alphabet="binary"), size_lo=1, size_hi=100)
    names = {e.name for e in derive_edge_cases(_io_schema(field))}
    assert {"max_stress", "all_same_char", "periodic"} <= names


def test_adversarial_contract_generation_deterministic_and_in_bounds() -> None:
    """파생 adversarial contract 전체 — 같은 seed 재현 + 모든 케이스가 제약 내(V/가중치/참조)."""
    schema = _graph_and_query_schema(2, 40)
    contract = derive_generator_contract(schema)
    a = generate_inputs(contract, schema, seed=13)
    b = generate_inputs(contract, schema, seed=13)
    assert [c.input_text for c in a] == [c.input_text for c in b]  # 결정론
    assert len(a) == contract.total_planned_cases
    for c in a:
        lines = c.input_text.split("\n")
        v, e = (int(x) for x in lines[0].split())
        assert 2 <= v <= 40  # size_range 존중
        for ln in lines[1 : 1 + e]:
            u, t, w = (int(x) for x in ln.split())
            assert 1 <= u <= v and 1 <= t <= v and u != t  # 1-indexed·self-loop 없음
            assert 1 <= w <= 9  # value_range 존중
        s, t2 = _query_value(c.input_text, 0), _query_value(c.input_text, 1)
        assert 1 <= s <= v and 1 <= t2 <= v  # 참조 스칼라 실제 V 바인딩


# ---------- 아키타입 최소 크기 보장 (카테고리명↔입력 정합 — 크기 1 추첨 fallback 수선) ----------


def test_duplicate_edges_guaranteed_with_size_lower_bound_one() -> None:
    """크기 하한 1 스키마 — V=1 추첨이 나와도 하한 2 클램프로 다중 간선 **항상** 실재."""
    schema = _io_schema(_weighted_edges_field(1, 12))
    for seed in range(12):
        v, _, edges = _parse_graph(_edge_case_text(schema, "duplicate_edges", seed=seed))
        pairs = [(u, t) for u, t, _ in edges]
        assert v >= 2  # V=1 fallback 불가 (하한 2 클램프)
        assert len(set(pairs)) < len(pairs)  # 같은 쌍 반복이 모든 seed 에서 실재


def test_periodic_guaranteed_min_length_two() -> None:
    """크기 하한 1 문자열 — 길이 1 추첨이 나와도 하한 2 클램프로 주기 반복 항상 실재."""
    field = _shaped_string_field(StringShape(alphabet="lowercase"), size_lo=1, size_hi=12)
    for seed in range(12):
        text = _edge_case_text(_io_schema(field), "periodic", seed=seed)
        n = len(text)
        assert n >= 2  # 길이 1 fallback 불가
        assert any(
            all(text[i] == text[i % p] for i in range(n)) for p in (2, 3)
        )  # 주기 2~3 반복이 모든 seed 에서 실재


def test_pairwise_sequence_archetypes_guarantee_min_two_elements() -> None:
    """교대/혼재/tie 아키타입 — 원소 2+ 전제라 크기 하한 1 스키마에서도 N≥2 보장."""
    field = IOFieldSpec(
        name="arr",
        type="int_array",
        size_range=ConstraintRange(name="arr", min_value=1, max_value=12),
        value_range=ConstraintRange(name="v", min_value=-5, max_value=5),
    )
    for name in ("alternating", "extreme_values", "all_equal"):
        for seed in range(8):
            vals = _array_values(_edge_case_text(_io_schema(field), name, seed=seed))
            assert len(vals) >= 2, (name, seed)


def test_graph_archetypes_guarantee_min_two_vertices() -> None:
    """graph 구조 아키타입 전부 — V=1 은 간선 0(공허)이라 하한 2 클램프로 간선 1+ 보장."""
    schema = _io_schema(_weighted_edges_field(1, 12))
    for name in ("path_chain", "star", "dense", "cycle_heavy", "equal_weights"):
        for seed in range(6):
            v, e, _ = _parse_graph(_edge_case_text(schema, name, seed=seed))
            assert v >= 2 and e >= 1, (name, seed)


def test_tree_archetypes_guarantee_min_two_vertices() -> None:
    """사슬/스타 트리 — 크기 하한 1 스키마에서도 V≥2 (단일 정점 '1' fallback 불가)."""
    field = IOFieldSpec(
        name="tree",
        type="tree_edges",
        size_range=ConstraintRange(name="V", min_value=1, max_value=12),
    )
    for name in ("path_chain", "star"):
        for seed in range(6):
            lines = _edge_case_text(_io_schema(field), name, seed=seed).split("\n")
            assert int(lines[0]) >= 2 and len(lines) >= 2, (name, seed)


def test_archetype_floor_skipped_when_size_upper_is_one() -> None:
    """size 상한 1 스키마 — 클램프 불가(범위 위반 금지) → 기본 경로 fallback 유지 (방어)."""
    schema = _io_schema(_weighted_edges_field(1, 1))
    v, e, _ = _parse_graph(_edge_case_text(schema, "duplicate_edges", seed=0))
    assert v == 1 and e == 0  # size_range 위반 없이 fallback
