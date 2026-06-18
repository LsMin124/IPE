"""IPE v2 CLI — P1/P2 생성 파이프라인 실행 (Phase 4 — 2-모드 수렴).

Usage::

    python -m ipe.v2.main_v2 --algorithm dijkstra --mode p1
    python -m ipe.v2.main_v2 --algorithm dijkstra --mode p2 --max-iter 6

env: ``ANTHROPIC_API_KEY`` (production LLM calls).

두 모드 다 **full 파이프라인**(synthesis+verification+풀 채점셋+QA)을 태운다 — 차이는
모드 노브뿐(``config.mode_knobs``):
- ``--mode p1`` (단일·공개): composition 빈값·hidden=False·QA 3종(leakage 제외).
  타겟 알고리즘 고정 공개(토픽 드릴).
- ``--mode p2`` (합성·은닉, 기본): composition≥1·hidden=True·QA 4종. 타겟은 힌트(은닉).

흐름: strategist→formalizer→narrative→faithfulness → spec_bridge → designer →
golden×K/brute fan-out → reconcile → executor → 풀 채점셋 → QA 게이트. golden 은
distinct 모델로 fan-out(차분 독립성). observability: stdout 요약(+``--verbose`` 전체).
output 영속화는 미포함(API/batch 경로가 담당).

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

from . import config
from .graph import build_v2_graph
from .state import DEFAULT_MAX_ITERATIONS, V2State, initial_v2_state

# recursion budget pad — 값은 config 단일 소스. 주석은 각 pad 의 근거(스테이지 tail).
# 모델링 루프 1회 = narrative+faithfulness+regen(3 step).
_RECURSION_PAD = config.RECURSION_PAD_BASE
# synthesis tail(spec_bridge→designer→fan-out→reconcile→bridge→executor) 단발 step.
_SYNTHESIS_RECURSION_PAD = config.RECURSION_PAD_SYNTHESIS
# suite tail(generator_designer→input_generator→suite_assembler) 단발 step.
_SUITE_RECURSION_PAD = config.RECURSION_PAD_SUITE
# qa tail(리뷰어 4종 병렬 superstep→aggregator) + back-route revise 사이클
# (routeback→narrative_revise→faithfulness_revise→spec_patch→재리뷰→집계).
_QA_RECURSION_PAD = config.RECURSION_PAD_QA

# golden fan-out 은 distinct 모델로(차분 독립성, §7.4). brute 는 별도 origin 라벨.
DEFAULT_GOLDEN_MODELS = config.GOLDEN_MODELS_CLI_DEFAULT
DEFAULT_BRUTE_MODEL = config.BRUTE_MODEL


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
        "--mode",
        choices=["p1", "p2"],
        default="p2",
        help="생성 모드 (p1=단일·공개·QA3 / p2=합성·은닉·QA4, default: p2)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="blueprint/narrative 전체 출력"
    )
    parser.add_argument(
        "--golden-models",
        default=DEFAULT_GOLDEN_MODELS,
        help=(
            "golden fan-out 모델 (comma-separated, distinct 권장, "
            f"default: {DEFAULT_GOLDEN_MODELS})"
        ),
    )
    parser.add_argument(
        "--brute-model",
        default=DEFAULT_BRUTE_MODEL,
        help=f"brute(naive) coder 모델 (default: {DEFAULT_BRUTE_MODEL})",
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
    if final.spec_authoring_error is not None:
        print(f"[v2] spec_authoring_error: {final.spec_authoring_error}")
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
    _print_suite_qa_summary(final)


def _print_suite_qa_summary(final: V2State) -> None:
    """채점셋/QA 산출물 요약 (--with-test-suite / --with-qa 시 populate)."""
    if final.generator_contract is not None:
        suite = final.test_suite
        print(
            f"[v2] test_suite: planned={final.generator_contract.total_planned_cases} "
            f"assembled={len(suite.cases) if suite is not None else 0} "
            f"golden_origin={suite.golden_origin if suite is not None else None}"
        )
    if final.qa_report is not None:
        q = final.qa_report
        verdicts = {r.kind: r.passed for r in q.reviews}
        print(
            f"[v2] qa: overall_pass={q.overall_pass} verdicts={verdicts} "
            f"failed_kinds={list(q.failed_kinds)} routebacks={final.qa_routebacks}"
        )
        for r in q.reviews:
            if not r.passed:
                for finding in r.findings:
                    print(f"  - [{r.kind}] {finding.severity}: {finding.description}")


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


def _build_default_graph(args: argparse.Namespace) -> Any:
    """production full 그래프 build (synthesis+suite+qa 항상). 모드 노브는 config.

    golden 은 ``--golden-models`` (comma-separated, distinct 권장) 각각을 fan-out 단위로,
    brute 는 ``--brute-model`` 1개. origin 라벨 = 모델명(차분 독립성 추적). hidden/
    composition_mode/qa_kinds 는 ``--mode`` 가 ``config.mode_knobs`` 로 결정.
    """
    golden_models = [m.strip() for m in args.golden_models.split(",") if m.strip()]
    if not golden_models:
        raise SystemExit("--golden-models 는 최소 1개 모델 필요")
    hidden, composition_mode, qa_kinds = config.mode_knobs(args.mode)
    return build_v2_graph(
        hidden=hidden,
        composition_mode=composition_mode,
        golden_llms=[
            AnthropicCoderLLM(m, parse_discipline=True) for m in golden_models
        ],
        brute_llm=AnthropicCoderLLM(args.brute_model, parse_discipline=True),
        golden_origins=golden_models,
        with_test_suite=True,
        with_qa=True,
        qa_kinds=qa_kinds,
        # suite/qa 노드 LLM 은 None → graph 의 production default(Opus/Haiku) 배선
    )


def main(argv: Sequence[str] | None = None, *, graph: Any = None) -> int:
    """CLI entrypoint. ``graph`` 주입 시 build 생략(test 결정론). exit 0=success."""
    load_dotenv()
    args = _build_parser().parse_args(argv)

    run_id = args.run_id or f"v2-{uuid.uuid4().hex[:8]}"
    hidden, composition_mode, qa_kinds = config.mode_knobs(args.mode)
    initial = initial_v2_state(run_id, args.algorithm, max_iterations=args.max_iter)

    print(
        f"[v2] start run_id={run_id} seed={args.algorithm.value} mode={args.mode} "
        f"hidden={hidden} composition={composition_mode} "
        f"qa_kinds={list(qa_kinds)} max_iter={args.max_iter}"
    )

    resolved = graph if graph is not None else _build_default_graph(args)
    # 두 모드 다 full 파이프라인 — 전 스테이지 pad 합산(고정).
    pad = (
        _RECURSION_PAD
        + _SYNTHESIS_RECURSION_PAD
        + _SUITE_RECURSION_PAD
        + _QA_RECURSION_PAD
    )
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
