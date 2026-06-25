"""SequenceBackbone — the second concrete ``AlgorithmBackbone`` (sequence/array
family: lis, sort, two_sum, binary_search, heap, fenwick, segtree). Owns the
``SequenceShape``-reading *behavior*: the semantic structural facts (sortedness /
duplicates as narrative DATA + faithfulness check).

Unlike the graph ``directed`` fact (a semantic overlay on identical ``u v w``
bytes), the sequence ``sortedness`` fact is a *byte-level* property — a sorted
array is literally different bytes from an unsorted one. So honoring it requires
the serializer (``input_gen._serialize_int_array``, a documented deferred coupling
in ``base.py``) to READ ``sequence_shape`` and emit sorted/distinct values. This
module owns only the *semantic* projection the skeleton consumes through the seam;
the serialization + format prose stay co-located in ``input_gen`` to avoid byte
drift, exactly as for graphs.

``derive_edge_inputs`` is reserved for G1b (realizable degeneracies: empty / min,
then value-pattern degeneracies once the serializer grows value-pattern biases) —
today it returns ``()`` (the graph-pre-5a shape), so adding this backbone is a pure
structural-facts addition with no edge-differential change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..generation.input_gen import derive_degenerate_inputs
from .base import DegenerateInput

if TYPE_CHECKING:
    from ipe.v1.schema import IOSchema

_SORTEDNESS_FACT = {
    "unsorted": "무정렬(임의 순서)",
    "non_decreasing": "비내림차 정렬(a[i] ≤ a[i+1])",
    "strictly_increasing": "순증가 정렬(a[i] < a[i+1], 중복 없음)",
}


class SequenceBackbone:
    """Sequence-family backbone. Owns a schema iff some ``int_array`` field carries a
    pinned ``SequenceShape`` (``None`` = legacy / byte-identical path with no facts to
    project, which falls through to ``NullBackbone``)."""

    name = "sequence"

    def owns(self, io_schema: IOSchema) -> bool:
        return any(
            f.type == "int_array" and f.sequence_shape is not None
            for f in io_schema.inputs
        )

    def structural_facts(self, io_schema: IOSchema) -> list[str]:
        """io_schema → sequence **structural facts** as machine-derived statements
        (single source of truth). The sortedness/duplicates facts that used to be
        un-decided anywhere — narrative receives these as DATA to *describe*, and
        faithfulness/QA check narrative ↔ facts by **machine comparison**.

        Only ``int_array`` fields with a pinned ``sequence_shape`` contribute
        (``None`` ⇒ skipped, so a non-sequence or legacy schema yields ``[]``).
        ``strictly_increasing`` already implies distinct, so the duplicates fact is
        omitted there (not restated) — avoids a redundant/contradictory statement.
        """
        facts: list[str] = []
        for f in io_schema.inputs:
            shape = f.sequence_shape
            if f.type != "int_array" or shape is None:
                continue
            prefix = f"{f.name}(수열)"
            facts.append(f"{prefix}: {_SORTEDNESS_FACT[shape.sortedness]}")
            if shape.sortedness != "strictly_increasing":
                dup = (
                    "중복 값 가능"
                    if shape.duplicates_allowed
                    else "중복 값 없음(서로 다른 값)"
                )
                facts.append(f"{prefix}: {dup}")
        return facts

    def derive_edge_inputs(self, io_schema: IOSchema) -> tuple[DegenerateInput, ...]:
        """**Active (G1b)** — realizable-degeneracy inputs for the reconcile differential
        (Tier-B uniqueness). Delegates to ``input_gen.derive_degenerate_inputs`` (the
        serialization-coupled degeneracy enumerator, a documented deferred coupling in
        ``base.py``) exactly as ``GraphBackbone`` does — for a sequence schema it yields
        the **min** witness (minimal-size array).

        Note empty and min *coincide* for sequences: ``empty`` is realizable only when
        ``size_range`` allows 0 (``_empty_safe``), and at that point ``bias="min"`` already
        emits the 0-length array — so there is no separate ``empty`` degeneracy to add (the
        realizable-today set is the single ``min`` witness).

        Value-pattern degeneracies (all-equal / sorted-tie / value-boundary) probe answer
        uniqueness under ties but need the serializer to grow value-pattern biases (RFC
        G1c); they are intentionally absent until then. Each agreeing golden ⇒ that minimal
        edge is well-posed and its output operationally defined; diverging ⇒ ill-posed IR
        with the minimal input as the witness.
        """
        return tuple(
            DegenerateInput(name=name, input_text=text, rationale=rationale)
            for name, text, rationale in derive_degenerate_inputs(io_schema)
        )
