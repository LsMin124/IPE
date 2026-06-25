"""SequenceBackbone unit tests (RFC backbone-generalization G1a).

Mirrors ``test_graph_backbone.py`` for the sequence family: the ``SequenceShape``
structural facts (narrative DATA + faithfulness check), ``resolve_backbone``
dispatch picking sequence over the ``NullBackbone`` fallback, and the
``owns`` realizability gate (int_array field with a *pinned* shape only).

``derive_edge_inputs`` (G1b) delegates to ``input_gen.derive_degenerate_inputs``
like ``GraphBackbone``, yielding the realizable ``min`` witness for the reconcile
differential (Tier-B uniqueness).
"""

from __future__ import annotations

from ipe.v1.schema import ConstraintRange, IOFieldSpec, IOSchema, SequenceShape
from ipe.v2.backbone import (
    DegenerateInput,
    NullBackbone,
    SequenceBackbone,
    resolve_backbone,
)


def _io_schema(field: IOFieldSpec) -> IOSchema:
    return IOSchema(inputs=(field,), output_type="int", output_format="x")


def _int_array_field() -> IOFieldSpec:
    return IOFieldSpec(
        name="arr",
        type="int_array",
        size_range=ConstraintRange(name="arr", min_value=1, max_value=20),
        value_range=ConstraintRange(name="v", min_value=1, max_value=100),
    )


def _shaped_array(shape: SequenceShape) -> IOFieldSpec:
    return _int_array_field().model_copy(update={"sequence_shape": shape})


def _scalar_int_field() -> IOFieldSpec:
    return IOFieldSpec(name="k", type="int")


# ---------- structural_facts (sortedness / duplicates as machine statements) ----------


def test_structural_facts_emits_sortedness_and_duplicates() -> None:
    field = _shaped_array(
        SequenceShape(sortedness="non_decreasing", duplicates_allowed=True)
    )
    joined = " | ".join(SequenceBackbone().structural_facts(_io_schema(field)))
    assert "비내림차" in joined  # non_decreasing
    assert "중복" in joined  # duplicates fact present
    assert "arr" in joined  # 필드명 prefix


def test_structural_facts_unsorted_distinct() -> None:
    field = _shaped_array(
        SequenceShape(sortedness="unsorted", duplicates_allowed=False)
    )
    joined = " | ".join(SequenceBackbone().structural_facts(_io_schema(field)))
    assert "무정렬" in joined
    assert "없음" in joined or "서로" in joined  # distinct


def test_structural_facts_strictly_increasing_omits_redundant_duplicate_clause() -> None:
    # strictly_increasing implies distinct — no separate duplicates fact emitted.
    field = _shaped_array(SequenceShape(sortedness="strictly_increasing"))
    facts = SequenceBackbone().structural_facts(_io_schema(field))
    joined = " | ".join(facts)
    assert "순증가" in joined
    # exactly one fact (the sortedness statement); duplicates is implied, not restated
    assert len(facts) == 1


def test_structural_facts_empty_without_shape_or_non_sequence() -> None:
    # int_array but unpinned (legacy / byte-identical) → no facts
    assert SequenceBackbone().structural_facts(_io_schema(_int_array_field())) == []
    # non-sequence type → no facts
    assert SequenceBackbone().structural_facts(_io_schema(_scalar_int_field())) == []


# ---------- owns / resolve_backbone / NullBackbone dispatch ----------


def test_sequence_backbone_owns_only_pinned_array_schema() -> None:
    backbone = SequenceBackbone()
    assert (
        backbone.owns(_io_schema(_shaped_array(SequenceShape(sortedness="unsorted"))))
        is True
    )
    # unpinned int_array → not owned (falls through to Null = legacy path)
    assert backbone.owns(_io_schema(_int_array_field())) is False
    assert backbone.owns(_io_schema(_scalar_int_field())) is False


def test_resolve_backbone_routes_sequence_to_sequence_backbone() -> None:
    resolved = resolve_backbone(
        _io_schema(_shaped_array(SequenceShape(sortedness="non_decreasing")))
    )
    assert resolved.name == "sequence"
    assert isinstance(resolved, SequenceBackbone)


def test_resolve_backbone_falls_back_to_null_for_unpinned_array() -> None:
    resolved = resolve_backbone(_io_schema(_int_array_field()))
    assert resolved.name == "none"
    assert isinstance(resolved, NullBackbone)


# ---------- derive_edge_inputs: G1b active (realizable "min" witness) ----------


def test_sequence_derive_edge_inputs_min_witness() -> None:
    # G1b delegates to derive_degenerate_inputs → "min" witness for the reconcile
    # differential (Tier-B uniqueness), mirroring GraphBackbone. Sequences aren't
    # separable, so no "unreachable"; empty coincides with min (no separate entry).
    field = _shaped_array(SequenceShape(sortedness="unsorted"))
    edges = SequenceBackbone().derive_edge_inputs(_io_schema(field))
    assert [e.name for e in edges] == ["min"]
    assert all(isinstance(e, DegenerateInput) for e in edges)
    assert all(e.input_text for e in edges)  # 비지 않은 직렬화 입력
    assert all(e.rationale for e in edges)  # 사람 설명 존재


def test_sequence_derive_edge_inputs_deterministic() -> None:
    # 고정 seed — 같은 io_schema 면 항상 같은 입력 (reconcile diff == edge_filler fill).
    schema = _io_schema(_shaped_array(SequenceShape(sortedness="non_decreasing")))
    first = SequenceBackbone().derive_edge_inputs(schema)
    second = SequenceBackbone().derive_edge_inputs(schema)
    assert first == second


def test_sequence_min_witness_respects_pinned_sortedness() -> None:
    # min 입력도 직렬화기를 거치므로 sortedness 핀을 존중 (정렬 배열이면 정렬된 min).
    field = _shaped_array(
        SequenceShape(sortedness="strictly_increasing"),
    )
    edges = SequenceBackbone().derive_edge_inputs(_io_schema(field))
    vals = [int(x) for x in edges[0].input_text.split("\n")[1].split()]
    assert vals == sorted(set(vals))  # 순증가·distinct (핀 일관)
