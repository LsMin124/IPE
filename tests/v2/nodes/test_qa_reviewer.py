"""QA 리뷰어 노드 단위 테스트 (Phase 3 M5 step2).

make_qa_reviewer_node(llm, kind=...) — 5종(N10a-e) 공용 factory:
- 병렬 fan-out 규율: **partial dict** ``{"qa_reviews": [review]}`` 반환 (M0/M2).
- freeze 규율: review.kind 는 node 의 kind 로 강제 스탬프 (LLM 이 못 바꿈).
- guard: 검토 대상 패키지(narrative+spec+test_suite) 없으면 ValueError.
"""

from __future__ import annotations

from typing import Any, get_args

import pytest

from ipe.v1.schema import (
    GeneratedTestCase,
    IOContract,
    IOFieldSpec,
    IOSchema,
    Narrative,
    ProblemBlueprint,
    ProblemSpec,
    QAReview,
    QAReviewerKind,
    SampleTestCase,
    TargetAlgorithm,
    TestSuite,
)
from ipe.v2.nodes import make_qa_reviewer_node
from ipe.v2.state import V2State, initial_v2_state

ALL_KINDS: tuple[QAReviewerKind, ...] = get_args(QAReviewerKind)


def _package_state() -> V2State:
    """QA 검토 대상 패키지(narrative+spec+suite)가 갖춰진 state."""
    base = initial_v2_state("run-qa", TargetAlgorithm.SORT)
    blueprint = ProblemBlueprint(
        reduction_core=TargetAlgorithm.SORT,
        domain="logistics",
        io_schema=IOSchema(
            inputs=(IOFieldSpec(name="N", type="int"),),
            output_type="int",
            output_format="단일 정수",
        ),
    )
    spec = ProblemSpec(
        target_algorithm=TargetAlgorithm.SORT,
        title="물류 정렬",
        description="은닉 지문",
        io_contract=IOContract(input_format="i", output_format="o"),
        sample_testcases=[
            SampleTestCase(input_text=str(i), expected_output=str(i))
            for i in range(1, 4)
        ],
    )
    suite = TestSuite(
        cases=(
            GeneratedTestCase(input_text="5", category="small", expected_output="5"),
        ),
        golden_origin="opus",
    )
    return base.model_copy(
        update={
            "blueprint": blueprint,
            "narrative": Narrative(
                title="물류 경로", scenario="물류 시나리오", hidden=True, domain="logistics"
            ),
            "spec": spec,
            "test_suite": suite,
        }
    )


class _FixedQALLM:
    def __init__(self, review: QAReview) -> None:
        self._review = review
        self.seen_state: Any = None

    def review(self, state: Any, *, kind: str) -> QAReview:
        self.seen_state = state
        return self._review


def test_reviewer_emits_partial_dict_with_review() -> None:
    node = make_qa_reviewer_node(
        _FixedQALLM(QAReview(kind="ambiguity", passed=True)), kind="ambiguity"
    )
    out = node(_package_state())
    assert isinstance(out, dict)  # 병렬 fan-out → partial dict (full state 금지)
    assert list(out.keys()) == ["qa_reviews"]
    assert out["qa_reviews"][0].passed is True


def test_reviewer_stamps_its_kind_over_llm_output() -> None:
    """LLM 이 엉뚱한 kind 를 반환해도 node 의 kind 로 강제 (freeze 규율)."""
    node = make_qa_reviewer_node(
        _FixedQALLM(QAReview(kind="difficulty", passed=False)), kind="leakage"
    )
    out = node(_package_state())
    assert out["qa_reviews"][0].kind == "leakage"


def test_reviewer_requires_package() -> None:
    node = make_qa_reviewer_node(
        _FixedQALLM(QAReview(kind="fairness", passed=True)), kind="fairness"
    )
    bare = initial_v2_state("r", TargetAlgorithm.SORT)  # 패키지 없음
    with pytest.raises(ValueError, match="spec"):
        node(bare)


def test_factory_builds_all_kinds() -> None:
    assert "presentation" in ALL_KINDS  # N10e 지문품질 kind 포함 (5종)
    for kind in ALL_KINDS:
        node = make_qa_reviewer_node(
            _FixedQALLM(QAReview(kind=kind, passed=True)), kind=kind
        )
        out = node(_package_state())
        assert out["qa_reviews"][0].kind == kind


def test_presentation_reviewer_stamps_kind() -> None:
    """N10e presentation — LLM 이 다른 kind 를 반환해도 presentation 으로 강제 스탬프."""
    node = make_qa_reviewer_node(
        _FixedQALLM(QAReview(kind="ambiguity", passed=True)), kind="presentation"
    )
    out = node(_package_state())
    assert out["qa_reviews"][0].kind == "presentation"


def test_presentation_charter_scopes_blocker_to_meaning_defects() -> None:
    """presentation charter — 상용 저지 수준 작문 게이트: 의미 전달을 해치는 결함만
    blocker, 문체 개선 여지는 warning/info (오탈락 방지 규율이 charter 에 명시)."""
    from ipe.v2.nodes.qa_reviewer import _CHARTERS

    charter = _CHARTERS["presentation"]
    assert "번역투" in charter  # 기계문체 점검
    assert "용어 일관성" in charter  # 같은 대상 다른 명칭 점검
    assert "blocker" in charter and "warning/info" in charter  # 심각도 규율


def test_user_prompt_shows_three_samples_and_suite_detail() -> None:
    """프롬프트 강화 — 샘플 3개 노출 + [채점셋 상세](분포+최대 크기 케이스 요약)."""
    from ipe.v2.nodes.qa_reviewer import _MAX_SAMPLES, _build_user_prompt

    prompt = _build_user_prompt(_package_state())
    assert _MAX_SAMPLES == 3
    assert "samples (앞 3개):" in prompt
    assert prompt.count("expected=") == 3  # 샘플 3개 전부 노출 (기존 2)
    assert "[채점셋 상세]" in prompt
    assert "카테고리 분포" in prompt
    assert "최대 입력 크기" in prompt  # 스트레스 케이스 존재 신호
    assert "대형 케이스" in prompt


def test_user_prompt_truncates_oversized_sample_text() -> None:
    """토큰 바운드 — 샘플 필드가 상한 초과면 절단 + '…(+N자)' 크기 표기만 남는다."""
    from ipe.v2.nodes.qa_reviewer import _SAMPLE_TEXT_LIMIT, _build_user_prompt

    big = "9" * (_SAMPLE_TEXT_LIMIT + 50)
    state = _package_state()
    spec = state.spec
    assert spec is not None
    fat_spec = spec.model_copy(
        update={
            "sample_testcases": [
                SampleTestCase(input_text=big, expected_output="1")
            ]
        }
    )
    prompt = _build_user_prompt(state.model_copy(update={"spec": fat_spec}))
    assert big not in prompt  # 원문 통짜 미노출
    assert "…(+50자)" in prompt  # 절단 표기


def test_easy_difficulty_charter_does_not_block_simplicity() -> None:
    """초급(is_basic) 완화 difficulty charter — '쉽다'는 이유로 막지 않고 진짜 퇴화만
    blocker. RFC 난이도-agnostic 원칙을 코드화. 표준 charter 와 구분(완화 적용)."""
    from ipe.v2.nodes.qa_reviewer import _CHARTERS, _DIFFICULTY_CHARTER_EASY

    assert "결함이 아니다" in _DIFFICULTY_CHARTER_EASY  # 단순함은 결함 아님
    assert "막지 말 것" in _DIFFICULTY_CHARTER_EASY  # '쉽다'고 막지 않음
    assert "상수" in _DIFFICULTY_CHARTER_EASY  # 진짜 퇴화(상수출력)는 여전히 차단
    assert _CHARTERS["difficulty"] != _DIFFICULTY_CHARTER_EASY  # 표준과 다름


def test_easy_presentation_charter_does_not_block_terseness() -> None:
    """초급(is_basic) 완화 presentation charter — narrative 초급 트랙의 의도된
    형식(1~2문단 교과서체, abstract 센티널의 시나리오 없는 맨 서술)을 '간결하다/
    시나리오가 없다'는 이유로 막지 않는다. 표준 charter 와 구분(완화 적용)."""
    from ipe.v2.nodes.qa_reviewer import (
        _CHARTERS,
        _EASY_CHARTERS,
        _PRESENTATION_CHARTER_EASY,
    )

    assert "결함이 아니다" in _PRESENTATION_CHARTER_EASY  # 간결·시나리오 부재는 결함 아님
    assert "시나리오 부재" in _PRESENTATION_CHARTER_EASY  # abstract 맨 서술 허용
    assert "번역투" in _PRESENTATION_CHARTER_EASY  # 문체 결함 점검은 유지
    assert "blocker" in _PRESENTATION_CHARTER_EASY  # 의미 훼손만 blocker 규율 유지
    assert _CHARTERS["presentation"] != _PRESENTATION_CHARTER_EASY  # 표준과 다름
    # dispatch 테이블 — difficulty/presentation 두 kind 만 완화 charter 를 갖는다.
    assert set(_EASY_CHARTERS) == {"difficulty", "presentation"}
    assert _EASY_CHARTERS["presentation"] is _PRESENTATION_CHARTER_EASY
