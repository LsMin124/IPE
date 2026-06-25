"""QA 리뷰어 노드 단위 테스트 (Phase 3 M5 step2).

make_qa_reviewer_node(llm, kind=...) — 4종(N10a-d) 공용 factory:
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


def test_factory_builds_all_four_kinds() -> None:
    for kind in ALL_KINDS:
        node = make_qa_reviewer_node(
            _FixedQALLM(QAReview(kind=kind, passed=True)), kind=kind
        )
        out = node(_package_state())
        assert out["qa_reviews"][0].kind == kind


def test_easy_difficulty_charter_does_not_block_simplicity() -> None:
    """초급(is_basic) 완화 difficulty charter — '쉽다'는 이유로 막지 않고 진짜 퇴화만
    blocker. RFC 난이도-agnostic 원칙을 코드화. 표준 charter 와 구분(완화 적용)."""
    from ipe.v2.nodes.qa_reviewer import _CHARTERS, _DIFFICULTY_CHARTER_EASY

    assert "결함이 아니다" in _DIFFICULTY_CHARTER_EASY  # 단순함은 결함 아님
    assert "막지 말 것" in _DIFFICULTY_CHARTER_EASY  # '쉽다'고 막지 않음
    assert "상수" in _DIFFICULTY_CHARTER_EASY  # 진짜 퇴화(상수출력)는 여전히 차단
    assert _CHARTERS["difficulty"] != _DIFFICULTY_CHARTER_EASY  # 표준과 다름
