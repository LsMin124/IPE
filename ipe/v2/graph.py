"""v2 LangGraph builder — 모델링 + synthesis/verification (+옵션 suite/QA).

Phase 4 (P1/P2 수렴): synthesis 는 **항상 배선**된다 (modeling-only 분기 제거 — 두
production 모드 다 full 검증). 모드 차이는 4 노브 (caller 가 조합)::

    노브               P1 (단일·공개)                   P2 (합성·은닉)
    hidden             False                            True
    composition_mode   "single" (composition 빈값)      "composed" (≥1, 총 2개+)
    qa_kinds           (ambiguity,fairness,difficulty)  +leakage (4종)
    seed_algorithm     고정 공개 타겟                   힌트

기본 흐름 (always)::

    START → strategist → formalizer → narrative → faithfulness → route ─┬─ regen→narrative
              (시드)      (FREEZE)     (렌더)       (round-trip)         │  (faithful=False)
                                          ▲                              │
                                          └──────────────────────────────┘
                       route(budget 소진) ── end_faithfulness
    faithfulness ─(faithful)→ spec_bridge → designer → dispatch ─┬→ golden_0..K ─┐
                                                                 └→ brute ───────┴→ reconciler
      reconciler ─(채택)→ synth_bridge → sample_filler → executor ─(pass)→ (suite/qa or success)
                                       (golden→sample expected)    └(fail)→ end_verification
      reconciler ─(reject)→ end_synthesis_rejected

``with_test_suite=True`` (M4 풀 채점셋 — verification 통과 후)::

    executor → route ─(pass)→ generator_designer → input_generator → suite_assembler
                                (contract 저작)     (결정론 생성)      (golden→expected)

``with_qa=True`` (M5 QA/Critic 병렬 게이트 — with_test_suite 필수, ``qa_kinds`` fan-out)::

    suite_assembler ─┬→ qa_{kind} (qa_kinds 병렬 fan-out, Haiku) ─┐
                     └ ...                                        ┴→ qa_aggregator → route
                                              ↑                      ├(pass)→ end_success
                       spec_patch(desc 만 패치) ← faithfulness_revise ┤(fail·예산)→ end_qa
                       (재리뷰 fan-out)            ← narrative_revise ┘(routeback, B)

핵심 의도:
- 모델링 4 노드(strategist/formalizer/narrative/faithfulness) + faithful=False→narrative
  재생성(``max_iterations`` 바운드). 왜곡만 reject, 은닉은 통과. strategist 는
  ``composition_mode`` 로 single(합성 금지)/composed(합성 필수) 분기.
- synthesis 는 **v1 M2 노드 재사용**(designer/synthesis_coder/reconciler/synth_bridge/
  executor) — V2State 가 design/attempt 채널 + target_algorithm property 로 적응(step2a).
  spec_bridge LLM 은 sample **input 만** 저작, expected 는 sample_filler 가 canonical
  golden 실행으로 채움(사용자 원칙: 정답은 golden 부트스트랩). golden↔brute differential
  + symbolic verifier 가 검증. fix-loop 없음(단발, M3+ 반복정제 별개).
- test-suite 는 **verification 통과 후에만** — expected 는 검증된 golden 실행으로
  부트스트랩(RFC §7 순환 회피)이라 검증 실패 경로에선 채점셋을 만들지 않는다.

recursion 주의: 모델링 루프 1회=3 step + synthesis ~6 step + test-suite 3 step.
``max_iterations`` 크면 invoke ``config={"recursion_limit": N}`` 상향.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from langgraph.graph import END, StateGraph

from ipe.sandbox.selector import pick_runner
from ipe.v1.nodes import (
    make_designer_node,
    make_executor_node,
    make_reconciler_node,
    make_synth_bridge_node,
    make_synthesis_coder_node,
)
from ipe.v1.router import route_after_full_executor, route_after_reconcile
from ipe.v1.verifiers import get_verifier

from .nodes import (
    FaithfulnessLLM,
    FormalizerLLM,
    GeneratorDesignerLLM,
    NarrativeLLM,
    QAReviewerLLM,
    SpecBridgeLLM,
    StrategistLLM,
    make_faithfulness_node,
    make_formalizer_node,
    make_generator_designer_node,
    make_input_generator_node,
    make_narrative_node,
    make_qa_aggregator_node,
    make_qa_reviewer_node,
    make_sample_filler_node,
    make_spec_bridge_node,
    make_spec_patch_node,
    make_strategist_node,
    make_suite_assembler_node,
)
from .router import route_after_faithfulness, route_after_qa, route_after_spec_bridge
from .state import V2FinalStatus, V2State

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from langgraph.graph.state import CompiledStateGraph

    from ipe.v1.nodes import CoderLLM, DesignerLLM, ExecutorRunner, VerifierGetter
    from ipe.v1.schema import QAReviewerKind

    from .nodes.strategist import CompositionMode


def _bump_iteration(state: V2State) -> V2State:
    """regen 노드 — faithfulness 실패 후 재시도 카운터 증가 (narrative 재생성 전)."""
    return state.model_copy(update={"iteration": state.iteration + 1})


def _bump_qa_routebacks(state: V2State) -> dict[str, Any]:
    """qa_routeback 노드 — back-route(B) 예산 1회 소비 (narrative revise 진입 전)."""
    return {"qa_routebacks": state.qa_routebacks + 1}


def _composed_aware_executor(
    *,
    runner: ExecutorRunner | None,
    verifier_getter: VerifierGetter,
) -> Callable[[V2State], V2State]:
    """M6 step1 — 합성 문제는 symbolic verifier 미적용 (Tier B 검증 정책).

    합성(blueprint.composition 비어있지 않음)되면 출력 의미가 reduction_core 단일
    알고리즘의 정석과 달라 그 symbolic verifier 가 옳은 풀이를 false-reject 한다
    (RFC §4 검증 천장). 합성 문제의 검증 신뢰 = 상류 reconcile(golden×K+brute
    distinct 합의, Tier B) + 샘플 일치 — M1 이 19-algo 로 Tier B≈Tier A 를 실증한
    근거 위에서 스위치한다. 비합성 문제는 기존 symbolic 경로 그대로 (anchor 보존).
    """
    symbolic = _v2_full_node(
        make_executor_node(runner=runner, verifier_getter=verifier_getter)
    )
    tier_b = _v2_full_node(
        make_executor_node(runner=runner, verifier_getter=lambda _a: None)
    )

    def node(state: V2State) -> V2State:
        bp = state.blueprint
        composed = bp is not None and len(bp.composition) > 0
        return tier_b(state) if composed else symbolic(state)

    return node


def _make_finalizer(status: V2FinalStatus) -> Callable[[V2State], V2State]:
    """terminal 노드 팩토리 — final_status set 후 END."""

    def node(state: V2State) -> V2State:
        return state.model_copy(update={"final_status": status})

    return node


def _fanout_dispatch(_state: V2State) -> dict[str, Any]:
    """synthesis fan-out trigger — state 무변경 (빈 partial dict)."""
    return {}


def _v2_full_node(v1_node: Any) -> Callable[[V2State], V2State]:
    """full-state 반환 v1 노드(designer/executor, ``-> V1State`` 주석)를 ``-> V2State``
    로 감싼다.

    langgraph 는 노드 return 주석으로 output-schema 를 추론한다. v1 노드의 ``V1State``
    주석을 그대로 쓰면 V1State.candidates(``operator.add`` reducer)가 V2State.candidates
    (``_merge_candidates`` reducer)와 채널 타입 충돌 → compile 실패. 이 래퍼가 return
    주석을 V2State 로 바꿔 그 추론을 막는다 (런타임은 이미 V2State 반환 — 무변화).
    """

    def node(state: V2State) -> V2State:
        return cast(V2State, v1_node(state))

    return node


def _v2_partial_node(v1_node: Any) -> Callable[[V2State], dict[str, Any]]:
    """partial-dict 반환 v1 노드(synthesis_coder/reconciler/synth_bridge)를 ``-> dict[
    str, Any]`` 로 감싼다.

    v1 노드 return 주석(예: ``dict[str, list[SolutionCandidate]]``)을 langgraph 가
    읽으면 candidates 등 typed write-channel 을 추론 → V2State 의 reducer 채널과 충돌.
    ``dict[str, Any]`` 주석은 그 추론을 막고 state-schema 채널(reducer 포함)에 그대로
    write 한다 (런타임 dict 무변화 — candidates accumulation/dedup 정상).
    """

    def node(state: V2State) -> dict[str, Any]:
        return cast("dict[str, Any]", v1_node(state))

    return node


def _v2_router(v1_router: Any) -> Callable[[V2State], str]:
    """v1 router(`route_after_reconcile`/`route_after_full_executor`, ``(V1State)`` 주석)
    를 ``(V2State)`` 입력 주석으로 감싼다.

    langgraph 는 conditional-edge path 함수의 입력 주석으로도 schema 를 추론한다. v1
    router 의 V1State 주석을 그대로 쓰면 V1State.candidates(``operator.add``)가 V2State
    의 ``_merge_candidates`` 채널과 충돌. 이 래퍼가 추론 schema 를 V2State 로 고정한다.
    """

    def route(state: V2State) -> str:
        return cast(str, v1_router(state))

    return route


def build_v2_graph(
    *,
    strategist_llm: StrategistLLM | None = None,
    formalizer_llm: FormalizerLLM | None = None,
    narrative_llm: NarrativeLLM | None = None,
    faithfulness_llm: FaithfulnessLLM | None = None,
    hidden: bool = True,
    composition_mode: CompositionMode = "composed",
    spec_bridge_llm: SpecBridgeLLM | None = None,
    designer_llm: DesignerLLM | None = None,
    golden_llms: Sequence[CoderLLM] | None = None,
    brute_llm: CoderLLM | None = None,
    golden_origins: Sequence[str] | None = None,
    brute_origin: str = "naive",
    runner: ExecutorRunner | None = None,
    verifier_getter: VerifierGetter = get_verifier,
    with_test_suite: bool = False,
    generator_designer_llm: GeneratorDesignerLLM | None = None,
    with_qa: bool = False,
    qa_reviewer_llms: Mapping[QAReviewerKind, QAReviewerLLM] | None = None,
    qa_kinds: tuple[QAReviewerKind, ...] = (
        "ambiguity",
        "fairness",
        "leakage",
        "difficulty",
    ),
) -> CompiledStateGraph:  # type: ignore[type-arg]
    """v2 그래프 빌드. None dependency 는 production default(Anthropic/sandbox/verifier).

    Phase 4 (P1/P2 수렴): synthesis 는 **항상 배선** — ``golden_llms``(>=1) +
    ``brute_llm`` 필수. ``hidden`` = narrative 렌더 모드. ``composition_mode`` =
    strategist 분기(``"single"``=합성 금지 / ``"composed"``=합성 필수). ``with_test_suite
    =True``(M4) 면 verification 통과 후 generator_designer→input_generator→
    suite_assembler 로 풀 채점셋까지. ``with_qa=True``(M5) 면 suite 완성 후 ``qa_kinds``
    리뷰어 병렬 게이트 — 완성 패키지를 검토하므로 ``with_test_suite=True`` 필수.
    ``qa_kinds`` = 돌릴 QA 관점(P1=ambiguity/fairness/difficulty 3종 / P2=+leakage 4종).
    ``qa_reviewer_llms`` 는 kind→LLM, 누락 kind 는 production Haiku. test 는 LLM mock 주입.
    """
    if with_qa and not with_test_suite:
        msg = "with_qa=True 는 with_test_suite=True 필수 (완성 패키지를 검토)"
        raise ValueError(msg)
    builder: StateGraph = StateGraph(V2State)  # type: ignore[type-arg]

    # ---- modeling nodes (always) ----
    builder.add_node(
        "strategist",
        cast(
            Any,
            make_strategist_node(strategist_llm, composition_mode=composition_mode),
        ),
    )
    builder.add_node("formalizer", cast(Any, make_formalizer_node(formalizer_llm)))
    builder.add_node(
        "narrative", cast(Any, make_narrative_node(narrative_llm, hidden=hidden))
    )
    builder.add_node(
        "faithfulness", cast(Any, make_faithfulness_node(faithfulness_llm))
    )
    builder.add_node("regen", cast(Any, _bump_iteration))
    builder.add_node("end_success", cast(Any, _make_finalizer("success")))
    builder.add_node(
        "end_faithfulness", cast(Any, _make_finalizer("fail_faithfulness"))
    )

    builder.set_entry_point("strategist")
    builder.add_edge("strategist", "formalizer")
    builder.add_edge("formalizer", "narrative")
    builder.add_edge("narrative", "faithfulness")
    builder.add_edge("regen", "narrative")  # 재생성 루프
    builder.add_edge("end_success", END)
    builder.add_edge("end_faithfulness", END)

    # faithful 통과 시 항상 synthesis 로 진행 (Phase 4 — modeling-only terminal 제거).
    builder.add_conditional_edges(
        "faithfulness",
        route_after_faithfulness,
        cast(
            Any,
            {
                "end_success": "spec_bridge",
                "regen": "regen",
                "end_faithfulness": "end_faithfulness",
            },
        ),
    )

    _wire_synthesis(
        builder,
        spec_bridge_llm=spec_bridge_llm,
        designer_llm=designer_llm,
        golden_llms=golden_llms,
        brute_llm=brute_llm,
        golden_origins=golden_origins,
        brute_origin=brute_origin,
        runner=runner,
        verifier_getter=verifier_getter,
        with_test_suite=with_test_suite,
        generator_designer_llm=generator_designer_llm,
        with_qa=with_qa,
        qa_reviewer_llms=qa_reviewer_llms,
        qa_kinds=qa_kinds,
        narrative_llm=narrative_llm,
        faithfulness_llm=faithfulness_llm,
        hidden=hidden,
    )

    return builder.compile()


def _wire_synthesis(
    builder: Any,
    *,
    spec_bridge_llm: SpecBridgeLLM | None,
    designer_llm: DesignerLLM | None,
    golden_llms: Sequence[CoderLLM] | None,
    brute_llm: CoderLLM | None,
    golden_origins: Sequence[str] | None,
    brute_origin: str,
    runner: ExecutorRunner | None,
    verifier_getter: VerifierGetter,
    with_test_suite: bool = False,
    generator_designer_llm: GeneratorDesignerLLM | None = None,
    with_qa: bool = False,
    qa_reviewer_llms: Mapping[QAReviewerKind, QAReviewerLLM] | None = None,
    qa_kinds: tuple[QAReviewerKind, ...] = (
        "ambiguity",
        "fairness",
        "leakage",
        "difficulty",
    ),
    narrative_llm: NarrativeLLM | None = None,
    faithfulness_llm: FaithfulnessLLM | None = None,
    hidden: bool = True,
) -> None:
    """synthesis 서브그래프 — spec_bridge→designer→fan-out→reconcile→bridge→executor.

    v1 M2 노드를 cast(Any) duck-typing 으로 재사용(step2a 가 V2State 적응으로 검증).
    ``with_test_suite=True`` 면 executor 통과 후 M4 채점셋 3 노드를 거쳐 end_success,
    ``with_qa=True`` 면 그 뒤 ``qa_kinds`` 병렬 게이트(M5)까지. ``narrative_llm``/
    ``faithfulness_llm``/``hidden`` 은 QA back-route(B)의 revise 경로용 passthrough.
    """
    if not golden_llms or brute_llm is None:
        msg = "synthesis 배선은 golden_llms(>=1) + brute_llm 필수"
        raise ValueError(msg)
    origins = (
        list(golden_origins)
        if golden_origins is not None
        else [f"golden-{i}" for i in range(len(golden_llms))]
    )
    if len(origins) != len(golden_llms):
        msg = f"golden_origins 길이({len(origins)}) != golden_llms({len(golden_llms)})"
        raise ValueError(msg)
    synth_runner: Any = runner if runner is not None else pick_runner()

    builder.add_node("spec_bridge", cast(Any, make_spec_bridge_node(spec_bridge_llm)))
    builder.add_node(
        "designer", cast(Any, _v2_full_node(make_designer_node(designer_llm)))
    )
    builder.add_node("dispatch", cast(Any, _fanout_dispatch))
    golden_names: list[str] = []
    for i, (llm, origin) in enumerate(zip(golden_llms, origins, strict=True)):
        name = f"golden_{i}"
        golden_names.append(name)
        builder.add_node(
            name,
            cast(
                Any,
                _v2_partial_node(
                    make_synthesis_coder_node(
                        llm, role="golden", origin=origin, fanout_index=i
                    )
                ),
            ),
        )
    builder.add_node(
        "brute",
        cast(
            Any,
            _v2_partial_node(
                make_synthesis_coder_node(
                    brute_llm, role="brute", origin=brute_origin, fanout_index=0
                )
            ),
        ),
    )
    builder.add_node(
        "reconciler", cast(Any, _v2_partial_node(make_reconciler_node(synth_runner)))
    )
    builder.add_node(
        "synth_bridge", cast(Any, _v2_partial_node(make_synth_bridge_node()))
    )
    # sample_filler — canonical golden 실행으로 sample expected 채움 (v2-native,
    # LLM 0). 사용자 원칙: 정답은 golden 부트스트랩. synth_bridge(attempt 확정) 후,
    # executor(검증) 전에 배선.
    builder.add_node(
        "sample_filler", cast(Any, make_sample_filler_node(runner=synth_runner))
    )
    builder.add_node(
        "executor",
        cast(
            Any,
            _composed_aware_executor(runner=runner, verifier_getter=verifier_getter),
        ),
    )
    builder.add_node(
        "end_verification", cast(Any, _make_finalizer("fail_verification"))
    )
    builder.add_node(
        "end_synthesis_rejected",
        cast(Any, _make_finalizer("fail_synthesis_rejected")),
    )
    builder.add_node(
        "end_spec_authoring", cast(Any, _make_finalizer("fail_spec_authoring"))
    )

    # spec 저작 실패(LLM structured output 전멸) 가드 — crash 대신 valid fail 종료
    builder.add_conditional_edges(
        "spec_bridge",
        cast(Any, route_after_spec_bridge),
        cast(
            Any,
            {"designer": "designer", "end_spec_authoring": "end_spec_authoring"},
        ),
    )
    builder.add_edge("end_spec_authoring", END)
    builder.add_edge("designer", "dispatch")
    for name in (*golden_names, "brute"):
        builder.add_edge("dispatch", name)  # fan-out (parallel superstep)
        builder.add_edge(name, "reconciler")  # fan-in (join once)
    builder.add_conditional_edges(
        "reconciler",
        _v2_router(route_after_reconcile),
        cast(
            Any,
            {
                "synth_bridge": "synth_bridge",
                "end_synthesis_rejected": "end_synthesis_rejected",
            },
        ),
    )
    builder.add_edge("synth_bridge", "sample_filler")
    builder.add_edge("sample_filler", "executor")
    # 검증 통과 시: 채점셋 생성(M4) 또는 즉시 success
    pass_target = "generator_designer" if with_test_suite else "end_success"
    builder.add_conditional_edges(
        "executor",
        _v2_router(route_after_full_executor),
        cast(
            Any,
            {
                "end_success": pass_target,
                "end_verification_fail": "end_verification",
            },
        ),
    )
    builder.add_edge("end_verification", END)
    builder.add_edge("end_synthesis_rejected", END)

    if with_test_suite:
        # M4: contract 저작(LLM) → 결정론 입력 생성 → verified golden 으로 expected.
        # 세 노드 모두 v2-native(V2State 주석) — v1 임피던스 래퍼 불요. full-state
        # 재emit 은 candidates dedup reducer 가 멱등 처리.
        builder.add_node(
            "generator_designer",
            cast(Any, make_generator_designer_node(generator_designer_llm)),
        )
        builder.add_node("input_generator", cast(Any, make_input_generator_node()))
        builder.add_node(
            "suite_assembler",
            cast(Any, make_suite_assembler_node(runner=synth_runner)),
        )
        builder.add_edge("generator_designer", "input_generator")
        builder.add_edge("input_generator", "suite_assembler")
        if with_qa:
            _wire_qa(
                builder,
                qa_reviewer_llms=qa_reviewer_llms,
                qa_kinds=qa_kinds,
                narrative_llm=narrative_llm,
                faithfulness_llm=faithfulness_llm,
                hidden=hidden,
            )
        else:
            builder.add_edge("suite_assembler", "end_success")


def _wire_qa(
    builder: Any,
    *,
    qa_reviewer_llms: Mapping[QAReviewerKind, QAReviewerLLM] | None,
    qa_kinds: tuple[QAReviewerKind, ...],
    narrative_llm: NarrativeLLM | None,
    faithfulness_llm: FaithfulnessLLM | None,
    hidden: bool,
) -> None:
    """QA 서브그래프 (M5, RFC N10/N11) — ``qa_kinds`` 병렬 게이트 + back-route(B).

    suite_assembler 에서 ``qa_kinds`` 리뷰어로 직접 fan-out(같은 superstep) → 각자
    partial dict 로 ``qa_reviews`` reducer 에 누적 → aggregator fan-in 집계(kind 별
    최신) → 결정론 라우팅. **back-route**: fail + 예산 잔여 시 narrative revise 재진입
    — QA findings 를 피드백으로 scenario 재작성(faithfulness 재게이트) 후 spec 의
    **description 만** 패치해 리뷰어 재리뷰. synthesis/verification/suite 는
    재실행하지 않는다 (io_contract·골든·채점셋이 진실원천, 지문을 고치는 방향).
    """
    for kind in qa_kinds:
        llm = qa_reviewer_llms.get(kind) if qa_reviewer_llms is not None else None
        builder.add_node(
            f"qa_{kind}", cast(Any, make_qa_reviewer_node(llm, kind=kind))
        )
        builder.add_edge("suite_assembler", f"qa_{kind}")  # fan-out
        builder.add_edge(f"qa_{kind}", "qa_aggregator")  # fan-in (join once)
    builder.add_node("qa_aggregator", cast(Any, make_qa_aggregator_node()))
    builder.add_node("end_qa", cast(Any, _make_finalizer("fail_qa")))
    builder.add_conditional_edges(
        "qa_aggregator",
        route_after_qa,
        cast(
            Any,
            {
                "end_success": "end_success",
                "routeback": "qa_routeback",
                "end_qa": "end_qa",
            },
        ),
    )
    builder.add_edge("end_qa", END)

    # ---- back-route revise 경로 (B, RFC N11 후반) ----
    # 예산 소비 → narrative 재작성(QA findings 피드백) → faithfulness 재게이트 →
    # description 패치 → 재리뷰. 'end_faithfulness' 종단은 modeling 배선의 것 재사용.
    builder.add_node("qa_routeback", cast(Any, _bump_qa_routebacks))
    builder.add_node(
        "narrative_revise",
        cast(Any, make_narrative_node(narrative_llm, hidden=hidden)),
    )
    builder.add_node(
        "faithfulness_revise", cast(Any, make_faithfulness_node(faithfulness_llm))
    )
    builder.add_node("regen_revise", cast(Any, _bump_iteration))
    builder.add_node("spec_patch", cast(Any, make_spec_patch_node()))

    builder.add_edge("qa_routeback", "narrative_revise")
    builder.add_edge("narrative_revise", "faithfulness_revise")
    builder.add_conditional_edges(
        "faithfulness_revise",
        route_after_faithfulness,  # 동일 결정론 — 타겟 매핑만 revise 경로용
        cast(
            Any,
            {
                "end_success": "spec_patch",
                "regen": "regen_revise",
                "end_faithfulness": "end_faithfulness",
            },
        ),
    )
    builder.add_edge("regen_revise", "narrative_revise")
    for kind in qa_kinds:
        builder.add_edge("spec_patch", f"qa_{kind}")  # 재리뷰 fan-out
