# RFC: Backbone Generalization — graph-overfit single-IR → all algorithm families

**Status:** proposed · **Date:** 2026-06-25 · **Depends on:** [single-IR architecture RFC](2026-06-23_single-ir-architecture-rfc.md) (Phases 0–5a, PR #172) · **Branch:** `backbone-generalization`

## 0. Thesis

The single-IR refactor made *structure* (graph self-loops / multi-edges / directedness / connectivity) a **machine-derived fact** that narrative describes and faithfulness/QA/reconcile check by comparison — collapsing the O(N²) contradiction surface to O(2). It did so **only for graphs**. The seam that was supposed to make this family-agnostic (`ipe/v2/backbone/`, commit `d2baef4`) is real and clean, but it has exactly **one** concrete (`GraphBackbone`); the other ~11 of 19 `TargetAlgorithm` families resolve to `NullBackbone` — zero structural facts, zero degeneracy witnesses. So the *benefit* (consistency-by-construction + Tier-B edge uniqueness) lands on graph problems only.

This RFC generalizes it: move the documented deferred-couplings behind the seam and add non-graph backbones, **with zero skeleton edits**. The success criterion is mechanical: a new family = one `_REGISTRY` append + one impl module. If generalizing forces a skeleton (node / graph-wiring / reconcile) edit, the seam design failed and that is a finding, not a workaround.

## 1. Current state (grounded)

**Seam contract** (`ipe/v2/backbone/base.py`): `AlgorithmBackbone` Protocol with three methods —
- `owns(io_schema) -> bool` — registry dispatch key.
- `structural_facts(io_schema) -> list[str]` — IR → machine-derived structural statements (narrative DATA + faithfulness check). The graph-1b mechanism.
- `derive_edge_inputs(io_schema) -> tuple[DegenerateInput, ...]` — IR → realizable-degeneracy inputs fed to the reconcile differential as Tier-B uniqueness evidence. The graph-5a mechanism.

**Skeleton consumption is exactly three sites, all via `resolve_backbone`:**

| Site | Call | Phase |
|------|------|-------|
| `ipe/v2/nodes/narrative.py:145` | `resolve_backbone(bp.io_schema).structural_facts(...)` | 1b |
| `ipe/v2/nodes/faithfulness.py:78` | `resolve_backbone(bp.io_schema).structural_facts(...)` | 1b |
| `ipe/v2/nodes/reconciler.py:58` | `resolve_backbone(io_schema).derive_edge_inputs(...)` | 5a |

`resolve_backbone` is total (first `owns` match wins, else `NullBackbone`) — callers never branch on `None`. This is why adding a backbone is skeleton-free: the three sites already call through the seam.

**The 19 families and today's coverage:**

| Group | Count | Algorithms | IOFieldType(s) | Backbone today |
|-------|-------|-----------|----------------|----------------|
| Graph | 8 | dijkstra, bfs, union_find, toposort, max_flow, bellman_ford, floyd_warshall, kruskal_mst | `weighted_edges`, `tree_edges` | **GraphBackbone** ✓ |
| Sequence | 7 | lis, sort, two_sum, binary_search, heap, fenwick, segtree | `int_array` (+ `int` query/target scalars) | NullBackbone |
| String | 1 | string_match | `string` | NullBackbone |
| DP/Grid | 2 | knapsack, coin_change | `int_array` + `int`, `int_matrix`/`grid` | NullBackbone |
| Number theory | 1 | sieve | `int` | NullBackbone |

The 9 `IOFieldType`s (`blueprint.py:27`): graph = `weighted_edges`·`tree_edges` (owned); non-graph = `int`·`int_array`·`int_matrix`·`float`·`string`·`bool`·`grid` (all unowned → no facts).

## 2. The generalization mapping table

Each backbone declares four axes. This table is the design contract for G1–G4; `GraphBackbone` is the worked reference for the first three columns and `derive_degenerate_inputs` for the fourth.

| Axis | Graph (done) | Sequence (G1) | String (G2) | DP/Grid (G3) | Number theory |
|------|--------------|---------------|-------------|--------------|---------------|
| **Shape type** (structural facts source) | `GraphShape` (directed / self_loops / multi_edges / connectivity) | **`SequenceShape`** (sortedness / duplicates_allowed / sign / distinct) | **`StringShape`** (alphabet / case_sensitive) | **`SequenceShape` reuse** (items) + **`GridShape`** (passability / diagonal) | (scalar — no shape) |
| **Latent-contradiction pin** (the "directedness" analog) | `directed` — undecided anywhere; serializer emits `u v w`, verifier assumes directed, narrative free | **`sortedness`** — binary_search *requires* sorted input; nothing decides or checks whether the array is sorted | `case_sensitive` / `alphabet` — match semantics depend on it | coin-system canonicality (greedy-correct?) ; grid passability convention | none material |
| **Realizable degeneracies** | min (minimal graph; folds s==t), unreachable (disconnected) | empty (N=0), single (N=1), all-equal, sorted-asc, sorted-desc, value-boundary | single-char, all-same, full-alphabet, periodic, no-match, pattern⊋text | zero-capacity, single-item, item>capacity, 1×1, 1-row, all-zero, all-blocked | 0, 1, smallest-prime (2), max-N, prime-vs-composite |
| **Output equivalence** | exact `==` | exact `==` | exact `==` | exact `==` | exact `==` |

**Float/geometry** (no family today, but the reason G4 exists): a `float`-output family needs **epsilon** equivalence, not exact `==`. That is the one axis the seam does *not* yet expose — reconcile's `differential.py:_normalize` hardcodes exact equality. G4 lifts equivalence to a backbone-declared checker.

## 3. The serializer reality (what makes a degeneracy "realizable")

A degeneracy is *realizable* only if the serializer can emit it format-validly. The reconcile differential and `sample_filler`/`edge_filler` must serialize the **same bytes** (fixed `_EDGE_SEED`), so degeneracy = a deterministic serialization, not a hand-written string.

Today's `_Bias = "random" | "empty" | "min" | "max" | "disconnected"` controls **size only** for arrays:

```python
def _serialize_int_array(field, tier_bound, rng, *, bias):
    n = _pick_size(_size_bounds(field, tier_bound), bias, rng)   # bias → size
    ...
    vals = " ".join(str(rng.randint(lo, hi)) for _ in range(n)) # values ALWAYS random
```

So for sequences:
- **Realizable today** (size-only bias): `empty` (N=0 via `bias="empty"`), `min` (minimal N via `bias="min"`). `derive_degenerate_inputs` already emits `min` for *any* sized field — it is simply never reached because non-graph schemas hit `NullBackbone.derive_edge_inputs → ()`.
- **Requires a serializer extension** (value-pattern bias): `all-equal`, `sorted-asc`, `sorted-desc`, `value-boundary`. The element loop must become pattern-aware.

This mirrors the graph realizability filter exactly: `GraphShape(connectivity="connected")` makes `unreachable` *unrealizable*, so it is absent from the set rather than emitted-and-wrong. The generalization rule is the same — **a backbone derives only the degeneracies its shape + the serializer can actually realize.**

**Consequence for sequencing the work:** the structural-facts half of any backbone is a pure projection (no serializer dependency, like `GraphBackbone.structural_facts`) and ships first; the degeneracy half splits into *realizable-today* (empty/min) and *needs-serializer-extension* (value patterns). This is the same 1b-then-5a staging the graph path used.

## 4. Deferred-coupling surface (from `base.py`)

`base.py` documents five couplings *not yet behind the seam*. Each generalizes at a specific G-step:

1. **Serializer family branch** (`_serialize_int_array` / `_serialize_int_matrix` / `_serialize_weighted_edges` / `_serialize_tree_edges` / `_backbone` / `_edge_key`) + **format prose** (`_structural_clause` / `_vertex_index_phrase` / `_render_field`, co-located with serialization to prevent byte drift). → each backbone owns its type's serialization + format prose. **G1+ (incremental, per type the backbone introduces).**
2. **Verifier dispatch** (`ipe/v1/verifiers/*`) + **`IOFieldType` enum**. → backbone-scoped types/verifiers. **G4.**
3. **Reconcile output-equivalence policy** (exact `==`, `differential.py:_normalize`). → epsilon/custom checker as a backbone axis. **G4.**
4. **`GraphShape`-on-`IOFieldSpec`** (`blueprint.py:42`) = graph-only structural fact. → per-family shape types (`SequenceShape`, `StringShape`, `GridShape`), each optional + defaulted (byte-identical until the formalizer pins them). **G1/G2/G3 add a shape type each.**
5. **`derive_degenerate_inputs`** (graph degeneracy only: min / unreachable). → per-family realizable degeneracy (the §2 row 3 catalog). **G1/G2/G3.**

Couplings 4 and 5 are the per-family work (G1–G3). Couplings 2 and 3 are the cross-cutting equivalence/verifier seam (G4). Coupling 1 rides along with whichever backbone first needs a given type serialized with patterns.

## 5. Migration plan (each shippable + measurable)

Mirrors the graph path: structural facts (1b-shaped) before edge inputs (5a-shaped). Every step is measured by **that family's P1 ship-rate (before/after)** and **edge-differential activation** (does `meta.resolved_edge_cases` populate for that family?), identical to the Phase 5a measurement protocol.

- **G1a — `SequenceShape` + `SequenceBackbone.structural_facts`** (pure projection; no serializer touch). Add `SequenceShape` (optional field on `IOFieldSpec`, default = today's implicit assumptions ⇒ byte-identical), `owns` (int_array field with a pinned shape), `structural_facts` (sortedness / duplicates / sign). Append to `_REGISTRY`. **Proves Null→concrete with zero skeleton edits.** Formalizer emits the shape (prompt). *Measure:* sequence-family `fail_qa` ambiguity; the "is it sorted?" contradiction class (binary_search) closes.
- **G1b — `SequenceBackbone.derive_edge_inputs`.** Ship the realizable-today set first (`empty`, `min`) — already serializable, immediate Tier-B witnessing for sequences (e.g. empty-array semantics of LIS/two_sum). Then extend the serializer with value-pattern biases (`all-equal`, `sorted`, `reverse`, `value-boundary`) behind coupling #1 and add them. *Measure:* sequence edge-differential activation; ill-posed-on-empty rejects.
- **G2 — `StringBackbone`** (string_match): `StringShape` (alphabet / case) + degeneracies (single-char, all-same, full-alphabet, periodic, no-match, pattern⊋text). Note `_STRING_MIN_LEN = 1` makes `empty` *unrealizable* today — it stays absent (graph-connectivity pattern), and lifting it is an explicit shape decision, not a silent default.
- **G3 — DP/Grid** (knapsack/coin_change/grid): reuse `SequenceShape` for item arrays; add `GridShape` for `int_matrix`/`grid`. Degeneracies: zero-capacity, single-item, item>capacity, 1×1, 1-row, all-zero, all-blocked.
- **G4 — verifier/reconcile equivalence seam** (couplings #2, #3): lift exact `==` to a backbone-declared output-equivalence checker (float epsilon, custom tie checkers), and route verifier dispatch / `IOFieldType` through the backbone. This is the step that lets a `float`-output family exist at all.

Ordering rationale: G1 is the **maximum-leverage first concrete** (7 families, one shape) and the cleanest Null→concrete proof; G2/G3 reuse its pattern; G4 is cross-cutting and sequenced last because no current family needs non-exact equivalence (so it is independently deferrable, exactly as Phase 5b was).

## 6. Risks and trade-offs

- **Skeleton edit = seam-failure signal.** The whole bet is that `owns`/`structural_facts`/`derive_edge_inputs` + `_REGISTRY` append suffice. If a family needs the narrative/faithfulness/reconciler nodes themselves to change, the Protocol is under-specified — surface it rather than patching the node.
- **Construction-enforced gap.** Formalizer emitting a shape is *prompt-enforced*, not code-forced (Phase 5a observed `bfs_run3` failing to pin `graph_shape` → silent `NullBackbone`). Generalization should let the **Phase-2 IR validator** require a family-appropriate shape (escalate prompt-enforced → construction-enforced) once a family's facts are load-bearing. Until then, an un-pinned field gracefully degrades to `NullBackbone` (byte-identical legacy path), so it is safe-by-default.
- **Realizability is the correctness boundary, not coverage.** A backbone must emit only degeneracies the shape + serializer realize (e.g. no `unreachable` under `connected`, no `empty` string under `_STRING_MIN_LEN=1`). An unrealizable degeneracy that leaks into the differential is a format-invalid input that the golden parser rejects — a bug, not a stricter test.
- **Shape defaults must equal today's implicit constants.** Every new shape field is optional with a default matching current serializer behavior, so each G-step is byte-identical until the formalizer varies it — the same zero-regression guarantee Phase 1 used. Round-trip serializer↔parser tests extend to each new field.

## 7. Key files

- Seam (impl + registry): `ipe/v2/backbone/base.py`, `ipe/v2/backbone/__init__.py`, `+ sequence.py / string.py / dp_grid.py`
- Shape types (enrich IR, optional + defaulted): `ipe/v1/schema/blueprint.py`
- Serializer + format prose (coupling #1, per type): `ipe/v2/generation/input_gen.py`
- Equivalence + verifier (coupling #2/#3, G4): `ipe/v1/nodes/reconciler.py` differential, `ipe/v1/verifiers/*`
- Skeleton (must stay unedited — the success check): `ipe/v2/nodes/narrative.py`, `ipe/v2/nodes/faithfulness.py`, `ipe/v2/nodes/reconciler.py`, `ipe/v2/graph.py`
- Tests (pattern): `tests/v2/backbone/test_graph_backbone.py` → `+ test_sequence_backbone.py` etc.
