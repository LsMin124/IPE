"""StringBackbone — the third concrete ``AlgorithmBackbone`` (string family:
string_match). Owns the ``StringShape``-reading *behavior*: the semantic structural
facts (alphabet as narrative DATA + faithfulness check) and the realizable
min-witness edge input.

Like sequence ``sortedness`` (and unlike graph ``directed``), the alphabet fact is a
*byte-level* property — a DNA string is literally different bytes from a lowercase
one. Honoring it requires the serializer (``input_gen._serialize_field`` string
branch, a documented deferred coupling in ``base.py``) to READ ``string_shape`` and
draw characters from the pinned alphabet. This module owns only the *semantic*
projection + the delegated degeneracy derivation; serialization + format prose stay
co-located in ``input_gen`` to avoid byte drift, exactly as for the other backbones.

``empty`` is unrealizable for strings (``_STRING_MIN_LEN=1`` / ``_empty_safe`` False)
— the graph connectivity-forbids-unreachable pattern — so the realizable-today edge
set is the single ``min`` witness (shortest string), delegated like the others.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..generation.input_gen import derive_degenerate_inputs
from .base import DegenerateInput

if TYPE_CHECKING:
    from ipe.v1.schema import IOSchema

_ALPHABET_FACT = {
    "lowercase": "영소문자 a-z",
    "uppercase": "영대문자 A-Z",
    "binary": "이진 문자 0/1",
    "dna": "DNA 염기 A/C/G/T",
    "alphanumeric": "영문자+숫자 a-zA-Z0-9",
}


class StringBackbone:
    """String-family backbone. Owns a schema iff some ``string`` field carries a
    pinned ``StringShape`` (``None`` = legacy / byte-identical lowercase path with no
    facts to project, which falls through to ``NullBackbone``)."""

    name = "string"

    def owns(self, io_schema: IOSchema) -> bool:
        return any(
            f.type == "string" and f.string_shape is not None
            for f in io_schema.inputs
        )

    def structural_facts(self, io_schema: IOSchema) -> list[str]:
        """io_schema → string **structural facts** (alphabet) as machine-derived
        statements. narrative receives these as DATA to *describe*, and
        faithfulness/QA check narrative ↔ facts by **machine comparison**.

        Only ``string`` fields with a pinned ``string_shape`` contribute (``None`` ⇒
        skipped, so a non-string or legacy schema yields ``[]``)."""
        facts: list[str] = []
        for f in io_schema.inputs:
            shape = f.string_shape
            if f.type != "string" or shape is None:
                continue
            facts.append(f"{f.name}(문자열): {_ALPHABET_FACT[shape.alphabet]}")
        return facts

    def derive_edge_inputs(self, io_schema: IOSchema) -> tuple[DegenerateInput, ...]:
        """Realizable-degeneracy inputs for the reconcile differential (Tier-B
        uniqueness). Delegates to ``input_gen.derive_degenerate_inputs`` (the
        serialization-coupled enumerator, a deferred coupling in ``base.py``) like the
        other backbones — for a string schema it yields the **min** witness (shortest
        string). ``empty`` is unrealizable (``_STRING_MIN_LEN=1``), so it is absent."""
        return tuple(
            DegenerateInput(name=name, input_text=text, rationale=rationale)
            for name, text, rationale in derive_degenerate_inputs(io_schema)
        )
