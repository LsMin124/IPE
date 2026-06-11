"""IPE v2 CLI — blueprint-first 은닉 모델링 그래프 실행 (Phase 3 M3 + 통합).

Usage::

    python -m ipe.v2.main_v2 --algorithm dijkstra
    python -m ipe.v2.main_v2 --algorithm knapsack --direct --max-iter 4
    python -m ipe.v2.main_v2 --algorithm dijkstra --with-synthesis

env: ``ANTHROPIC_API_KEY`` (production LLM calls).

기본 범위: **모델링 layer** (strategist→formalizer→narrative→faithfulness, 4 호출).
``--with-synthesis`` 면 faithful 통과 후 **synthesis+verification** 까지 — spec_bridge
→ designer → golden×K/brute fan-out → reconcile → executor (검증된 정답까지 생성).
golden 은 distinct 모델로 fan-out(차분 독립성). observability: stdout 요약(+``--verbose``
전체). output 영속화는 미포함(follow-up).

exit code: 0 on ``success``, 1 on any ``fail_*``.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence
from typing import Any

from dotenv import load_dotenv

from ipe.v1.nodes import AnthropicCoderLLM
from ipe.v1.schema import TargetAlgorithm

from .graph import build_v2_graph
from .state import DEFAULT_MAX_ITERATIONS, V2State, initial_v2_state

# 모델링 루프 1회 = narrative+faithfulness+regen(3 step). recursion budget 여유.
_RECURSION_PAD = 15
# synthesis tail(spec_bridge→designer→fan-out→reconcile→bridge→executor) 단발 step 여유.
_SYNTHESIS_RECURSION_PAD = 12

# golden fan-out 은 distinct 모델로(차분 독립성, §7.4). brute 는 별도 origin 라벨.
DEFAULT_GOLDEN_MODELS = "claude-opus-4-7,claude-sonnet-4-6"
DEFAULT_BRUTE_MODEL = "claude-sonnet-4-6"


def _parse_target_algorithm(value: str) -> TargetAlgorithm:
    """argparse type — 지원 enum value 검증 (seed 알고리즘)."""
    try:
        return TargetAlgorithm(value.lower())
    except ValueError as e:
        valid = [a.value for a in TargetAlgorithm]
        msg = f"unsupported algorithm '{value}'. supported: {valid}"
        raise argparse.ArgumentTypeError(msg) from e


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ipe-v2",
        description=(
            "IPE v2 — blueprint-first 은닉 모델링 "
            "(strategist→formalizer→narrative→faithfulness)"
        ),
    )
    parser.add_argument(
        "--algorithm",
        type=_parse_target_algorithm,
        required=True,
        help="은닉할 seed 알고리즘 (예: dijkstra, knapsack)",
    )
    parser.add_argument(
        "--run-id", default=None, help="run identifier (default: random)"
    )
    parser.add_argument(
        "--max-iter",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help=f"narrative 재생성 budget (default: {DEFAULT_MAX_ITERATIONS})",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="직접(B2C) 렌더 — 알고리즘 명시 (default: 은닉 B2B)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="blueprint/narrative 전체 출력"
    )
    parser.add_argument(
        "--with-synthesis",
        action="store_true",
        help="faithful 후 synthesis+verification 까지 (검증된 정답 생성, 비용↑)",
    )
    parser.add_argument(
        "--golden-models",
        default=DEFAULT_GOLDEN_MODELS,
        help=(
            "golden fan-out 모델 (comma-separated, distinct 권장). "
            f"--with-synthesis 시만 (default: {DEFAULT_GOLDEN_MODELS})"
        ),
    )
    parser.add_argument(
        "--brute-model",
        default=DEFAULT_BRUTE_MODEL,
        help=(
            "brute(naive) coder 모델. --with-synthesis 시만 "
            f"(default: {DEFAULT_BRUTE_MODEL})"
        ),
    )
    return parser


def _print_summary(final: V2State) -> None:
    """final state 요약 stdout (모델링 layer observability)."""
    print(f"[v2] final_status={final.final_status} iteration={final.iteration}")
    if final.strategy is not None:
        s = final.strategy
        print(
            f"[v2] strategy: reduction_core={s.reduction_core.value} "
            f"composition={[a.value for a in s.composition]} domain={s.domain!r}"
        )
    if final.blueprint is not None:
        bp = final.blueprint
        inputs = [f"{f.name}:{f.type}" for f in bp.io_schema.inputs]
        print(
            f"[v2] blueprint.io: inputs={inputs} "
            f"output={bp.io_schema.output_type}"
        )
        print(f"[v2] blueprint.invariants: {[iv.kind for iv in bp.output_invariants]}")
    if final.narrative is not None:
        print(
            f"[v2] narrative: hidden={final.narrative.hidden} "
            f"domain={final.narrative.domain!r}"
        )
    if final.faithfulness is not None:
        f = final.faithfulness
        print(
            f"[v2] faithfulness: faithful={f.faithful} "
            f"distortions={len(f.distortions)}"
        )
        for d in f.distortions:
            print(f"  - {d}")
    _print_synthesis_summary(final)


def _print_synthesis_summary(final: V2State) -> None:
    """synthesis layer 산출물 요약 (--with-synthesis 시 populate)."""
    if final.spec is not None:
        print(
            f"[v2] spec: title={final.spec.title!r} "
            f"target_algorithm={final.spec.target_algorithm.value}"
        )
    if final.candidates:
        origins = [c.origin for c in final.candidates]
        print(f"[v2] candidates: {len(final.candidates)} origins={origins}")
    if final.reconciliation is not None:
        r = final.reconciliation
        print(
            f"[v2] reconciliation: all_agree={r.all_agree} "
            f"adopted_origin={r.adopted_origin}"
        )
        for d in r.disagreements:
            print(f"  - {d}")
    if final.verification is not None:
        print(f"[v2] verification: overall_pass={final.verification.overall_pass}")


def _print_verbose(final: V2State) -> None:
    """blueprint/narrative 전체 — 진단용."""
    print("\n=== VERBOSE ===")
    if final.blueprint is not None:
        print(f"[blueprint.domain] {final.blueprint.domain}")
        print(f"[blueprint.io.output_format] {final.blueprint.io_schema.output_format}")
    if final.narrative is not None:
        print(f"\n[narrative.scenario]\n{final.narrative.scenario}")
    print("=== END VERBOSE ===\n")


def _normalize_final_state(raw: object) -> V2State:
    """LangGraph invoke 반환은 dict 또는 V2State — 항상 V2State 로 변환."""
    if isinstance(raw, V2State):
        return raw
    return V2State.model_validate(raw)


def _build_default_graph(args: argparse.Namespace, *, hidden: bool) -> Any:
    """production 그래프 build. ``--with-synthesis`` 면 golden/brute coder LLM 배선.

    golden 은 ``--golden-models`` (comma-separated, distinct 권장) 각각을 fan-out 단위로,
    brute 는 ``--brute-model`` 1개. origin 라벨 = 모델명(차분 독립성 추적).
    """
    if not args.with_synthesis:
        return build_v2_graph(hidden=hidden)
    golden_models = [m.strip() for m in args.golden_models.split(",") if m.strip()]
    if not golden_models:
        raise SystemExit("--golden-models 는 최소 1개 모델 필요")
    return build_v2_graph(
        hidden=hidden,
        with_synthesis=True,
        golden_llms=[AnthropicCoderLLM(m) for m in golden_models],
        brute_llm=AnthropicCoderLLM(args.brute_model),
        golden_origins=golden_models,
    )


def main(argv: Sequence[str] | None = None, *, graph: Any = None) -> int:
    """CLI entrypoint. ``graph`` 주입 시 build 생략(test 결정론). exit 0=success."""
    load_dotenv()
    args = _build_parser().parse_args(argv)

    run_id = args.run_id or f"v2-{uuid.uuid4().hex[:8]}"
    hidden = not args.direct
    initial = initial_v2_state(run_id, args.algorithm, max_iterations=args.max_iter)

    print(
        f"[v2] start run_id={run_id} seed={args.algorithm.value} "
        f"hidden={hidden} max_iter={args.max_iter} "
        f"with_synthesis={args.with_synthesis}"
    )

    resolved = (
        graph if graph is not None else _build_default_graph(args, hidden=hidden)
    )
    pad = _RECURSION_PAD + (_SYNTHESIS_RECURSION_PAD if args.with_synthesis else 0)
    raw = resolved.invoke(
        initial, config={"recursion_limit": 3 * args.max_iter + pad}
    )
    final = _normalize_final_state(raw)

    _print_summary(final)
    if args.verbose:
        _print_verbose(final)

    return 0 if final.final_status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
