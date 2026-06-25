"""StringBackbone unit tests (RFC backbone-generalization G2).

Mirrors ``test_sequence_backbone.py`` for the string family: ``StringShape``
alphabet structural facts, ``resolve_backbone`` dispatch over the ``NullBackbone``
fallback, the ``owns`` realizability gate (string field with a *pinned* shape only),
and the delegated ``min`` edge-input witness.
"""

from __future__ import annotations

from ipe.v1.schema import ConstraintRange, IOFieldSpec, IOSchema, StringShape
from ipe.v2.backbone import (
    DegenerateInput,
    NullBackbone,
    StringBackbone,
    resolve_backbone,
)


def _io_schema(field: IOFieldSpec) -> IOSchema:
    return IOSchema(inputs=(field,), output_type="int", output_format="x")


def _string_field() -> IOFieldSpec:
    return IOFieldSpec(
        name="s",
        type="string",
        size_range=ConstraintRange(name="s", min_value=1, max_value=20),
    )


def _shaped_string(shape: StringShape) -> IOFieldSpec:
    return _string_field().model_copy(update={"string_shape": shape})


def _scalar_int_field() -> IOFieldSpec:
    return IOFieldSpec(name="k", type="int")


# ---------- structural_facts (alphabet as machine statements) ----------


def test_structural_facts_emits_alphabet() -> None:
    field = _shaped_string(StringShape(alphabet="dna"))
    joined = " | ".join(StringBackbone().structural_facts(_io_schema(field)))
    assert "DNA" in joined or "A/C/G/T" in joined  # dna alphabet
    assert "s" in joined  # 필드명 prefix


def test_structural_facts_binary() -> None:
    field = _shaped_string(StringShape(alphabet="binary"))
    joined = " | ".join(StringBackbone().structural_facts(_io_schema(field)))
    assert "이진" in joined or "0/1" in joined


def test_structural_facts_empty_without_shape_or_non_string() -> None:
    # string but unpinned (legacy / byte-identical lowercase) → no facts
    assert StringBackbone().structural_facts(_io_schema(_string_field())) == []
    # non-string type → no facts
    assert StringBackbone().structural_facts(_io_schema(_scalar_int_field())) == []


# ---------- owns / resolve_backbone / NullBackbone dispatch ----------


def test_string_backbone_owns_only_pinned_string_schema() -> None:
    backbone = StringBackbone()
    assert (
        backbone.owns(_io_schema(_shaped_string(StringShape(alphabet="lowercase"))))
        is True
    )
    # unpinned string → not owned (falls through to Null = legacy lowercase path)
    assert backbone.owns(_io_schema(_string_field())) is False
    assert backbone.owns(_io_schema(_scalar_int_field())) is False


def test_resolve_backbone_routes_string_to_string_backbone() -> None:
    resolved = resolve_backbone(_io_schema(_shaped_string(StringShape(alphabet="dna"))))
    assert resolved.name == "string"
    assert isinstance(resolved, StringBackbone)


def test_resolve_backbone_falls_back_to_null_for_unpinned_string() -> None:
    resolved = resolve_backbone(_io_schema(_string_field()))
    assert resolved.name == "none"
    assert isinstance(resolved, NullBackbone)


# ---------- derive_edge_inputs: delegated "min" witness ----------


def test_string_derive_edge_inputs_min_witness() -> None:
    # Delegates to derive_degenerate_inputs → "min" (shortest string). No "empty"
    # (unrealizable, _STRING_MIN_LEN=1), no "unreachable" (not separable).
    field = _shaped_string(StringShape(alphabet="lowercase"))
    edges = StringBackbone().derive_edge_inputs(_io_schema(field))
    assert [e.name for e in edges] == ["min"]
    assert all(isinstance(e, DegenerateInput) for e in edges)
    assert all(e.input_text for e in edges)  # 비지 않은 직렬화 입력
    assert all(e.rationale for e in edges)
