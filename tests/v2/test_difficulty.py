"""난이도 calibration 에이전트 (RFC R4) 테스트 — 스키마 + evaluate/annotate + 백필.

파이프라인 그래프는 난이도-agnostic 이고, 난이도는 완성 패키지의 사후 주석이다.
v0 calibration anchor(``ipe.calibration``)를 재사용하되 v2 typed 구조(structured
output)로 산출한다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ipe.v1.schema import DifficultyFactors, DifficultyReport


def _report(label: str = "Gold IV") -> DifficultyReport:
    return DifficultyReport(
        label=label,
        reasoning="bj_1753_gold4 와 동형 — 단일 출발점 다익스트라, V≈20000.",
        factors=DifficultyFactors(
            algorithm="dijkstra",
            complexity="O((V+E) log V)",
            n_max=20000,
            data_structures=("priority_queue", "adjacency_list"),
        ),
        calibration_anchors=("bj_1753_gold4",),
    )


# --------------------------------------------------------------------------- #
# 스키마 — DifficultyReport / DifficultyFactors                                #
# --------------------------------------------------------------------------- #
def test_report_derives_tier_from_label() -> None:
    assert _report("Gold IV").tier == "Gold"
    assert _report("Platinum V").tier == "Platinum"
    assert _report("Bronze V").tier == "Bronze"


def test_report_serializes_tier_into_dump() -> None:
    """computed tier 가 model_dump 에 포함 — 백엔드 필터·집계용."""
    dumped = _report("Gold IV").model_dump()
    assert dumped["tier"] == "Gold"
    assert dumped["label"] == "Gold IV"
    assert dumped["factors"]["algorithm"] == "dijkstra"


def test_report_rejects_unknown_tier_label() -> None:
    """label 선두가 BOJ 티어가 아니면 reject (왜곡된 구조화 출력 차단)."""
    with pytest.raises(ValidationError):
        _report("Wizard IX")
    with pytest.raises(ValidationError):
        _report("   ")  # 공백뿐 → 선두 티어 없음


def test_report_is_frozen_and_extra_forbid() -> None:
    assert DifficultyReport.model_config.get("frozen") is True
    r = _report()
    with pytest.raises(ValidationError):
        r.label = "Silver V"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        DifficultyReport(
            label="Gold IV",
            reasoning="x",
            factors=r.factors,
            bogus=1,  # type: ignore[call-arg]
        )


def test_factors_require_nonempty_algorithm_and_complexity() -> None:
    with pytest.raises(ValidationError):
        DifficultyFactors(algorithm="", complexity="O(1)")
    with pytest.raises(ValidationError):
        DifficultyFactors(algorithm="impl", complexity="")


# --------------------------------------------------------------------------- #
# evaluate_difficulty / annotate_difficulty (mock LLM 주입)                     #
# --------------------------------------------------------------------------- #
_ANCHORS: list[dict[str, object]] = [
    {
        "id": "bj_1753_gold4",
        "label": "Gold V",
        "summary": "단일 출발점 다익스트라",
        "factors": {"algorithm": "dijkstra", "n_max": 20000},
    },
    {
        "id": "bj_1000_bronze5",
        "label": "Bronze V",
        "summary": "A+B",
        "factors": {"algorithm": "implementation", "n_max": 1},
    },
]


class _FakeDifficultyLLM:
    """주입형 mock — 고정 report 반환 + 마지막 호출 인자 기록."""

    def __init__(self, report: DifficultyReport) -> None:
        self._report = report
        self.seen_package: dict[str, object] | None = None
        self.seen_anchors: list[dict[str, object]] | None = None

    def evaluate(
        self, package: dict[str, object], *, anchors: list[dict[str, object]]
    ) -> DifficultyReport:
        self.seen_package = package
        self.seen_anchors = anchors
        return self._report


def _package() -> dict[str, object]:
    return {
        "problem": {
            "title": "최단 비용",
            "description": "그래프에서 단일 출발점 최단경로 비용을 구한다.",
            "io_contract": {"input_format": "V E\\n...", "output_format": "최단거리"},
            "constraints": [{"name": "V", "min_value": 1, "max_value": 20000}],
            "sample_testcases": [{"input_text": "2 1\\n1 2 3", "expected_output": "3"}],
        },
        "solution": {"golden_code": "import heapq\\n# dijkstra", "language": "python"},
        "test_suite": {"cases": [], "origin": "opus"},
        "meta": {"hidden_algorithm": "dijkstra", "composition": []},
    }


def test_evaluate_difficulty_returns_report_and_forwards_anchors() -> None:
    from ipe.v2.difficulty import evaluate_difficulty

    llm = _FakeDifficultyLLM(_report("Gold IV"))
    out = evaluate_difficulty(_package(), llm=llm, anchors=_ANCHORS)
    assert out.label == "Gold IV"
    assert out.tier == "Gold"
    assert llm.seen_anchors == _ANCHORS  # anchors 전달
    assert llm.seen_package is not None


def test_evaluate_filters_hallucinated_anchor_ids() -> None:
    """LLM 이 제공 집합 밖 anchor id 를 인용하면 교집합 필터로 제거."""
    from ipe.v2.difficulty import evaluate_difficulty

    rep = _report().model_copy(
        update={"calibration_anchors": ("bj_1753_gold4", "bj_9999_fake")}
    )
    out = evaluate_difficulty(_package(), llm=_FakeDifficultyLLM(rep), anchors=_ANCHORS)
    assert out.calibration_anchors == ("bj_1753_gold4",)  # 환각 id drop


def test_evaluate_defaults_to_loaded_anchors() -> None:
    """anchors=None → load_anchors() 사용 (실제 anchors.json)."""
    from ipe.v2.difficulty import evaluate_difficulty

    llm = _FakeDifficultyLLM(_report())
    evaluate_difficulty(_package(), llm=llm)
    assert llm.seen_anchors  # 비어있지 않은 로드 anchor
    assert any(a.get("id") == "bj_1753_gold4" for a in llm.seen_anchors)  # 실제 anchors.json


def test_annotate_difficulty_is_immutable() -> None:
    from ipe.v2.difficulty import annotate_difficulty

    pkg = _package()
    out = annotate_difficulty(pkg, llm=_FakeDifficultyLLM(_report("Gold IV")), anchors=_ANCHORS)
    assert out["meta"]["difficulty"]["label"] == "Gold IV"  # type: ignore[index]
    assert out["meta"]["difficulty"]["tier"] == "Gold"  # type: ignore[index]
    assert "difficulty" not in pkg["meta"]  # type: ignore[operator]  # 원본 무변경
    assert out is not pkg


def test_annotate_difficulty_noop_on_empty_package() -> None:
    from ipe.v2.difficulty import annotate_difficulty

    assert annotate_difficulty({}, llm=_FakeDifficultyLLM(_report())) == {}


def test_user_prompt_includes_solution_and_anchor_ids() -> None:
    from ipe.v2.difficulty import _build_user_prompt

    text = _build_user_prompt(_package(), _ANCHORS)
    assert "bj_1753_gold4" in text  # anchor 블록
    assert "dijkstra" in text  # 정해/내부 힌트
    assert "최단경로" in text  # 지문


def test_system_prompt_has_tier_rubric_and_calibration_rules() -> None:
    """system prompt 는 절대 티어 rubric + 강화 규율(3-anchor 인용, 복잡도×n_max
    교차확인, 보수적 하향)을 포함 — anchor 상대 비교의 보정 프레임 회귀 가드."""
    from ipe.v2.difficulty import _SYSTEM_PROMPT

    # 티어 rubric — Bronze~Diamond 절대 프레임
    for tier in ("Bronze", "Silver", "Gold", "Platinum", "Diamond"):
        assert f"- {tier}:" in _SYSTEM_PROMPT
    # 강화 규율 3종
    assert "최소 3개" in _SYSTEM_PROMPT  # anchor 인용 하한
    assert "복잡도 × n_max 교차확인" in _SYSTEM_PROMPT  # 복잡도-규모 정합 체크
    assert "보수적으로 낮은 쪽" in _SYSTEM_PROMPT  # 애매하면 하향
    # 기존 규율 보존 (약화 금지)
    assert "상대 위치" in _SYSTEM_PROMPT
    assert "제공된 anchor id 중에서만" in _SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# DB 적재 — meta.difficulty.label → problems.difficulty 1급 컬럼                #
# --------------------------------------------------------------------------- #
def test_persist_writes_difficulty_column() -> None:
    """annotate → persist_run round-trip: difficulty 컬럼 + internal_meta 보존."""
    from sqlalchemy import create_engine, select

    from ipe.v2.db import init_schema, persist_run
    from ipe.v2.db.schema import problems
    from ipe.v2.difficulty import annotate_difficulty

    pkg = annotate_difficulty(
        _package(), llm=_FakeDifficultyLLM(_report("Gold IV")), anchors=_ANCHORS
    )
    body = {
        "batch": {"run_id": "diff-rt", "seed": "dijkstra", "mode": "full"},
        "final_status": "success",
        "package": pkg,
    }
    engine = create_engine("sqlite://")
    init_schema(engine)
    pid = persist_run(engine, body)
    assert pid is not None
    with engine.connect() as c:
        row = c.execute(select(problems).where(problems.c.id == pid)).mappings().one()
        assert row["difficulty"] == "Gold IV"  # 1급 컬럼 승격
        assert row["internal_meta"]["difficulty"]["tier"] == "Gold"  # 전체 report 보존


def test_persist_difficulty_null_when_unannotated() -> None:
    """난이도 미주석 패키지 → difficulty 컬럼 NULL (기존 동작 보존)."""
    from sqlalchemy import create_engine, select

    from ipe.v2.db import init_schema, persist_run
    from ipe.v2.db.schema import problems

    body = {
        "batch": {"run_id": "diff-null", "seed": "dijkstra", "mode": "full"},
        "final_status": "success",
        "package": _package(),  # meta.difficulty 없음
    }
    engine = create_engine("sqlite://")
    init_schema(engine)
    pid = persist_run(engine, body)
    with engine.connect() as c:
        row = c.execute(select(problems).where(problems.c.id == pid)).mappings().one()
        assert row["difficulty"] is None


# --------------------------------------------------------------------------- #
# 백필 — 기존 problems 행을 calibration (RFC R4 운영: 기존 27문제)               #
# --------------------------------------------------------------------------- #
def _seed_problem(engine: object, run_id: str, *, annotated: bool = False) -> str:
    from ipe.v2.db import persist_run
    from ipe.v2.difficulty import annotate_difficulty

    pkg = _package()
    if annotated:
        pkg = annotate_difficulty(
            pkg, llm=_FakeDifficultyLLM(_report("Gold IV")), anchors=_ANCHORS
        )
    body = {
        "batch": {"run_id": run_id, "seed": "dijkstra", "mode": "full"},
        "final_status": "success",
        "package": pkg,
    }
    pid = persist_run(engine, body)  # type: ignore[arg-type]
    assert pid is not None
    return pid


def test_backfill_fills_null_rows() -> None:
    from sqlalchemy import create_engine, select

    from ipe.v2.db import init_schema
    from ipe.v2.db.schema import problems
    from ipe.v2.difficulty import backfill_difficulty

    engine = create_engine("sqlite://")
    init_schema(engine)
    pid = _seed_problem(engine, "bf-1")

    done = backfill_difficulty(
        engine, llm=_FakeDifficultyLLM(_report("Silver III")), anchors=_ANCHORS
    )
    assert done == [(pid, "Silver III")]
    with engine.connect() as c:
        row = c.execute(select(problems).where(problems.c.id == pid)).mappings().one()
        assert row["difficulty"] == "Silver III"
        assert row["internal_meta"]["difficulty"]["tier"] == "Silver"


def test_backfill_skips_already_annotated_rows() -> None:
    """difficulty 있는 행은 건드리지 않음 (NULL 행만 — 멱등·재실행 안전)."""
    from sqlalchemy import create_engine

    from ipe.v2.db import init_schema
    from ipe.v2.difficulty import backfill_difficulty

    engine = create_engine("sqlite://")
    init_schema(engine)
    _seed_problem(engine, "bf-annotated", annotated=True)  # difficulty=Gold IV
    null_pid = _seed_problem(engine, "bf-null")  # difficulty NULL

    done = backfill_difficulty(
        engine, llm=_FakeDifficultyLLM(_report("Bronze V")), anchors=_ANCHORS
    )
    assert done == [(null_pid, "Bronze V")]  # NULL 행만


def test_backfill_dry_run_measures_without_writing() -> None:
    from sqlalchemy import create_engine, select

    from ipe.v2.db import init_schema
    from ipe.v2.db.schema import problems
    from ipe.v2.difficulty import backfill_difficulty

    engine = create_engine("sqlite://")
    init_schema(engine)
    pid = _seed_problem(engine, "bf-dry")

    done = backfill_difficulty(
        engine,
        llm=_FakeDifficultyLLM(_report("Platinum V")),
        anchors=_ANCHORS,
        dry_run=True,
    )
    assert done == [(pid, "Platinum V")]  # 측정은 반환
    with engine.connect() as c:
        row = c.execute(select(problems).where(problems.c.id == pid)).mappings().one()
        assert row["difficulty"] is None  # write 안 됨


def test_backfill_force_recalibrates_annotated_rows() -> None:
    """force=True → 이미 난이도 있는 행도 재측정·덮어쓰기 (anchor 확장 후 일관 재calibration)."""
    from sqlalchemy import create_engine, select

    from ipe.v2.db import init_schema
    from ipe.v2.db.schema import problems
    from ipe.v2.difficulty import backfill_difficulty

    engine = create_engine("sqlite://")
    init_schema(engine)
    pid = _seed_problem(engine, "bf-force", annotated=True)  # 기존 difficulty=Gold IV

    done = backfill_difficulty(
        engine,
        llm=_FakeDifficultyLLM(_report("Silver V")),
        anchors=_ANCHORS,
        force=True,
    )
    assert (pid, "Silver V") in done  # 이미 주석된 행도 재처리
    with engine.connect() as c:
        row = c.execute(select(problems).where(problems.c.id == pid)).mappings().one()
        assert row["difficulty"] == "Silver V"  # 기존 Gold IV → 덮어써짐
