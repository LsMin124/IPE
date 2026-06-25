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
    """V=200000=7.8MB 입력 실측 회귀 — bias=max 그래프도 _MAX_ELEMENTS 로 바운드."""
    schema = _io_schema(_weighted_edges_field(2, 50000))  # 본래 대형 그래프
    contract = GeneratorContract(
        scale_families=(ScaleFamily(name="s", case_count=1),),
        edge_cases=(EdgeCaseSpec(name="max_stress"),),  # bias=max → 상한 크기
    )
    edge = next(
        c for c in generate_inputs(contract, schema, seed=7) if c.category == "max_stress"
    )
    _, e, _ = _parse_graph(edge.input_text)
    assert e <= _MAX_ELEMENTS  # 총 간선 캡
    assert len(edge.input_text) < 1_500_000  # 수MB → 수백KB


def test_max_scale_array_input_is_element_capped() -> None:
    """대형 배열(OOM 유발) bias=max 도 N ≤ _MAX_ELEMENTS."""
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
    assert int(edge.input_text.split("\n")[0]) <= _MAX_ELEMENTS  # N


def test_max_scale_matrix_total_elements_capped() -> None:
    """행렬 R*C 원소 총량도 _MAX_ELEMENTS 로 캡 (R*C 폭주 방지)."""
    field = IOFieldSpec(
        name="m",
        type="int_matrix",
        size_range=ConstraintRange(name="m", min_value=1, max_value=200),
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
    assert r * c <= _MAX_ELEMENTS


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
    """weighted_edges(shape=None 레거시) — small/large tier + 4 실현가능 edge."""
    schema = _io_schema(_derive_edges_field())
    assert {f.name for f in derive_scale_families(schema)} == {"small", "large"}
    assert {e.name for e in derive_edge_cases(schema)} == {
        "min_size",
        "max_size",
        "empty",
        "disconnected",
    }


def test_derive_int_array_min_one_excludes_empty() -> None:
    """크기 하한 1 배열 — empty(크기 0)는 제약(크기≥1) 위반이라 미방출(realizability gate)."""
    schema = _io_schema(
        IOFieldSpec(
            name="arr",
            type="int_array",
            size_range=ConstraintRange(name="arr", min_value=1, max_value=50),
        )
    )
    assert {e.name for e in derive_edge_cases(schema)} == {"min_size", "max_size"}


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
    assert names == {"min_size", "max_size"}  # V_min=2 트리는 간선 1개 — empty 거짓


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
