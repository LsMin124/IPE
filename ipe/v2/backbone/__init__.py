"""Algorithm-backbone seam package. See ``base.py`` for the seam contract and the
deferred-coupling boundary; ``graph.py`` for the sole concrete (``GraphBackbone``).

The universal pipeline skeleton reaches all algorithm-family-specific structural
behavior through ``resolve_backbone(io_schema)`` only — adding a new backbone is one
append to ``_REGISTRY`` plus its impl module, with zero skeleton edits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import AlgorithmBackbone, DegenerateInput, NullBackbone
from .graph import GraphBackbone
from .sequence import SequenceBackbone
from .string import StringBackbone

if TYPE_CHECKING:
    from ipe.v1.schema import IOSchema

# Backbone registry — first ``owns`` match wins. Adding a family = one entry here.
# Families are disjoint by IOFieldType (graph=edges, sequence=int_array, string=string),
# so order is immaterial today; ``owns`` realizability keeps dispatch unambiguous.
_REGISTRY: tuple[AlgorithmBackbone, ...] = (
    GraphBackbone(),
    SequenceBackbone(),
    StringBackbone(),
)
_NULL: AlgorithmBackbone = NullBackbone()


def resolve_backbone(io_schema: IOSchema) -> AlgorithmBackbone:
    """Pick the backbone that owns this schema (first registry match), else the
    ``NullBackbone`` fallback. Total by construction — never returns ``None``."""
    for backbone in _REGISTRY:
        if backbone.owns(io_schema):
            return backbone
    return _NULL


__all__ = [
    "AlgorithmBackbone",
    "DegenerateInput",
    "GraphBackbone",
    "NullBackbone",
    "SequenceBackbone",
    "StringBackbone",
    "resolve_backbone",
]
