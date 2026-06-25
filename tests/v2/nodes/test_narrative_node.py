"""Narrative 노드 단위 테스트 (Phase 3 M3 step3, late 렌더).

- ``make_narrative_node``: frozen blueprint → Narrative (state.narrative). LLM 은
  scenario(NarrativeDraft)만 산출 → 노드가 hidden(graph config) + domain(blueprint
  carry-over) 스탬프. 렌더 모드/도메인은 node-authoritative (freeze 규율).

mock LLM 으로 sandbox/네트워크 없이 결정론 검증.
"""

from __future__ import annotations

import pytest

from ipe.v1.schema import (
    ConstraintRange,
    GraphShape,
    IOFieldSpec,
    IOSchema,
    Narrative,
    NarrativeDraft,
    OutputInvariant,
    ProblemBlueprint,
    QAFinding,
    QAReport,
    QAReview,
    TargetAlgorithm,
)
from ipe.v2.nodes import make_narrative_node
from ipe.v2.state import V2State, initial_v2_state


def _blueprint() -> ProblemBlueprint:
    return ProblemBlueprint(
        reduction_core=TargetAlgorithm.DIJKSTRA,
        composition=(TargetAlgorithm.BINARY_SEARCH,),
        domain="logistics",
        io_schema=IOSchema(
            inputs=(IOFieldSpec(name="N", type="int"),),
            output_type="int",
            output_format="단일 정수",
        ),
        output_invariants=(
            OutputInvariant(kind="non_negative", description="거리는 음수 불가"),
        ),
    )


def _state_with_blueprint() -> V2State:
    base = initial_v2_state("run-v2", TargetAlgorithm.DIJKSTRA)
    return base.model_copy(update={"blueprint": _blueprint()})


class _RecordingNarrativeLLM:
    """고정 title/scenario 를 반환하고 받은 hidden 플래그를 기록하는 mock."""

    def __init__(self, scenario: str, title: str = "배송 센터 경로 비용") -> None:
        self._scenario = scenario
        self._title = title
        self.received_hidden: bool | None = None

    def render(self, state: V2State, *, hidden: bool) -> NarrativeDraft:
        self.received_hidden = hidden
        return NarrativeDraft(title=self._title, scenario=self._scenario)


def test_narrative_renders_hidden_by_default() -> None:
    llm = _RecordingNarrativeLLM("물류 센터의 배송 경로 ...")
    out = make_narrative_node(llm)(_state_with_blueprint())

    nar = out.narrative
    assert isinstance(nar, Narrative)
    assert nar.title == "배송 센터 경로 비용"  # draft.title 스탬프 (creative slot 1)
    assert nar.scenario == "물류 센터의 배송 경로 ..."  # LLM 산출
    assert nar.hidden is True  # 기본 B2B 은닉
    assert nar.domain == "logistics"  # blueprint carry-over
    assert llm.received_hidden is True  # 노드가 hidden 플래그 전달


def test_narrative_direct_mode_when_hidden_false() -> None:
    llm = _RecordingNarrativeLLM("다익스트라로 최단경로를 구하라")
    out = make_narrative_node(llm, hidden=False)(_state_with_blueprint())

    nar = out.narrative
    assert isinstance(nar, Narrative)
    assert nar.hidden is False
    assert llm.received_hidden is False


def test_narrative_domain_and_hidden_are_node_authoritative() -> None:
    """LLM 은 scenario 만 산출 → domain/hidden 은 노드가 결정 (freeze 규율)."""
    llm = _RecordingNarrativeLLM("scenario text")
    out = make_narrative_node(llm, hidden=True)(_state_with_blueprint())

    nar = out.narrative
    assert isinstance(nar, Narrative)
    # NarrativeDraft 에는 domain/hidden 필드가 없으므로 LLM 이 영향 못 줌
    assert nar.domain == "logistics"  # blueprint.domain 그대로
    assert nar.hidden is True


def test_narrative_requires_blueprint() -> None:
    bare = initial_v2_state("r", TargetAlgorithm.BFS)  # blueprint 없음
    node = make_narrative_node(_RecordingNarrativeLLM("x"))
    with pytest.raises(ValueError, match="blueprint"):
        node(bare)


def test_narrative_preserves_original_state() -> None:
    state = _state_with_blueprint()
    out = make_narrative_node(_RecordingNarrativeLLM("s"))(state)
    assert state.narrative is None  # 원본 불변
    assert out.narrative is not None
    assert out.blueprint is state.blueprint  # blueprint 보존


def _graph_blueprint() -> ProblemBlueprint:
    """graph_shape 핀된 weighted_edges 형상 — backbone(structural_facts) 이 사실 방출."""
    return ProblemBlueprint(
        reduction_core=TargetAlgorithm.DIJKSTRA,
        domain="logistics",
        io_schema=IOSchema(
            inputs=(
                IOFieldSpec(
                    name="edges",
                    type="weighted_edges",
                    size_range=ConstraintRange(name="V", min_value=2, max_value=100),
                    value_range=ConstraintRange(name="w", min_value=1, max_value=9),
                    graph_shape=GraphShape(directed=False, self_loops=False),
                ),
            ),
            output_type="int",
            output_format="단일 정수",
        ),
    )


def test_narrative_prompt_instructs_hidden_safe_title() -> None:
    """Phase 4: narrative 가 title(creative slot 1)을 저작하되 은닉/유출 규율을 따르도록
    지시 (spec_bridge Opus 호출 강등으로 제목 저작이 narrative 로 접힘). 드리프트 방지."""
    from ipe.v2.nodes.narrative import _SYSTEM_PROMPT

    assert "title:" in _SYSTEM_PROMPT  # 제목 저작 지령
    assert "은닉" in _SYSTEM_PROMPT  # 은닉 모드 누설 금지


def test_narrative_prompt_instructs_structural_facts_description() -> None:
    """Phase 1b: narrative 가 '구조 사실' DATA 와 일치하게 서술하도록 지시 (prose 규칙
    대신 데이터 기반). 데이터 모순 = faithfulness reject. 드리프트 방지."""
    from ipe.v2.nodes.narrative import _SYSTEM_PROMPT

    assert "구조 사실" in _SYSTEM_PROMPT
    assert "모순" in _SYSTEM_PROMPT  # 데이터 모순 구조 금지


def test_narrative_user_prompt_includes_structural_facts_for_graph() -> None:
    """graph_shape 핀된 필드면 user prompt 에 구조 사실 DATA 주입 (narrative 가 서술)."""
    from ipe.v2.nodes.narrative import _build_user_prompt

    state = initial_v2_state("r", TargetAlgorithm.DIJKSTRA).model_copy(
        update={"blueprint": _graph_blueprint()}
    )
    prompt = _build_user_prompt(state, hidden=True)
    assert "구조 사실" in prompt
    assert "양방향" in prompt  # directed=False 투영


def test_narrative_user_prompt_omits_structural_facts_for_non_graph() -> None:
    """비-graph(int 필드만)면 구조 사실 섹션 미주입 — 회귀 안전(_blueprint 는 int)."""
    from ipe.v2.nodes.narrative import _build_user_prompt

    prompt = _build_user_prompt(_state_with_blueprint(), hidden=True)
    assert "구조 사실" not in prompt


def test_narrative_user_prompt_includes_qa_feedback_on_routeback() -> None:
    """back-route(B) 재진입 시 직전 QA 실패 findings 가 user prompt 에 렌더 —
    blind re-roll 이 아니라 지적 해소 방향의 재작성. 첫 pass/통과 report 는 미포함."""
    from ipe.v2.nodes.narrative import _build_user_prompt

    base = _state_with_blueprint()
    assert "QA" not in _build_user_prompt(base, hidden=True)

    failed = base.model_copy(
        update={
            "qa_report": QAReport(
                reviews=(
                    QAReview(
                        kind="ambiguity",
                        passed=False,
                        rationale="형식 모순",
                        findings=(
                            QAFinding(
                                severity="blocker",
                                description="입력 형식 모순 발견",
                            ),
                        ),
                    ),
                    QAReview(kind="fairness", passed=True),
                )
            )
        }
    )
    prompt = _build_user_prompt(failed, hidden=True)
    assert "QA" in prompt  # 피드백 섹션 존재
    assert "ambiguity" in prompt  # 실패 kind
    assert "입력 형식 모순 발견" in prompt  # finding 본문
    assert "fairness" not in prompt  # 통과 리뷰는 미포함

    ok = base.model_copy(
        update={
            "qa_report": QAReport(
                reviews=(QAReview(kind="ambiguity", passed=True),)
            )
        }
    )
    assert "QA" not in _build_user_prompt(ok, hidden=True)  # 통과면 미포함


def test_narrative_abstract_prompt_drops_domain_story() -> None:
    """domain==ABSTRACT_DOMAIN 용 abstract system prompt — 스토리 없이 변수로 맨 서술
    (초급 orthogonal abstract 선택, 스토리 군더더기 제거). 도메인 시나리오 prompt 와 구분."""
    from ipe.v2.nodes.narrative import _ABSTRACT_SYSTEM_PROMPT, _SYSTEM_PROMPT

    assert "도메인 스토리 없이" in _ABSTRACT_SYSTEM_PROMPT
    assert "지어내지 말 것" in _ABSTRACT_SYSTEM_PROMPT  # 현실 상황 날조 금지
    assert "A 와 B" in _ABSTRACT_SYSTEM_PROMPT  # 변수 직접 서술 예시
    assert _ABSTRACT_SYSTEM_PROMPT != _SYSTEM_PROMPT  # 도메인 시나리오 prompt 와 다름
