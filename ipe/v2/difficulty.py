"""난이도 calibration 에이전트 (RFC R4) — 완성 패키지의 사후 난이도 판별.

v2 파이프라인 그래프는 RFC 설계상 **난이도-agnostic** 이다(QA ``difficulty`` charter 는
*퇴화/모순 sanity* 만 본다). 본 모듈은 그와 **별개**로, 출하 패키지(problem+solution+
test_suite+meta)를 BOJ 표준 난이도 anchor(``ipe.calibration``)와 비교해 티어를 측정하는
**사후 주석**이다 — 출하 게이트가 아니라 메타데이터.

설계(RFC R4 결정):
- 입력은 **패키지 dict** = 전 생성경로(full v2 그래프 / canonical / hybrid)와 DB 백필의
  공통 분모 → in-graph 노드 대신 패키지-레벨 순수 함수로 모든 경로를 1개 함수로 커버.
- v0 ``ipe.nodes.evaluator`` 의 프롬프트·anchor 블록을 이식하되, JSON 파싱 대신 v2 의
  typed structured output(``DifficultyReport``)을 쓴다.
- 인용 anchor id 는 로드된 anchor 집합으로 교집합 필터(환각 id 제거).

``annotate_difficulty`` 는 ``meta.difficulty`` 를 채운 **새 package** 를 불변 반환한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from ipe.calibration import load_anchors
from ipe.v1.schema import DifficultyReport

from .config import DIFFICULTY_MODEL

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

# 프롬프트 바운드 — 정해 코드/샘플이 폭주해도 토큰 상한 유지.
_MAX_SOLUTION_CHARS = 4000
_MAX_SAMPLES = 3
_SAMPLE_FIELD_CHARS = 120

_SYSTEM_PROMPT = """\
당신은 코딩테스트 문제의 **난이도 calibrator** 다. 검증된 문제(지문 + 제약 + 정해 +
샘플)를 아래 BOJ 표준 난이도 anchor 와 비교해 난이도를 추정한다.

규율:
- label 은 anchor 라벨 양식으로: 선두는 반드시 티어 중 하나
  (Bronze/Silver/Gold/Platinum/Diamond/Ruby) + 로마숫자 등급 (예: "Gold IV",
  "Silver III", "Platinum II").
- reasoning 에는 비교에 쓴 **anchor id 를 명시**한다 (예: "bj_1753_gold5 와 동형 —
  둘 다 단일 출발점 다익스트라, V≈20000"). 사후 검수 가능성을 위해 필수.
- factors: 지배 알고리즘 / 시간복잡도 / 입력규모(n_max) / 핵심 자료구조.
- calibration_anchors: 비교한 anchor id 목록 — **제공된 anchor id 중에서만**.

절대 척도를 환각하지 말고 anchor 대비 **상대 위치**로 판단하라.
"""


def _build_anchor_block(anchors: list[dict[str, Any]]) -> str:
    """anchors → markdown 블록 (LLM 비교 참조). v0 evaluator 에서 이식."""
    if not anchors:
        return "## Calibration Anchors\n\n(anchor 없음)\n"
    import json

    lines = ["## Calibration Anchors (BOJ 표준 난이도별 reference)", ""]
    for a in anchors:
        aid = a.get("id", "?")
        label = a.get("label", "?")
        summary = a.get("summary", "")
        factors = a.get("factors") or {}
        lines.append(f"### {aid} — {label}")
        lines.append(f"- summary: {summary}")
        lines.append(f"- factors: {json.dumps(factors, ensure_ascii=False)}")
        lines.append("")
    return "\n".join(lines)


def _render_constraints(constraints: list[dict[str, Any]]) -> str:
    rendered = [
        f"{c.get('name', '?')} ∈ [{c.get('min_value')}, {c.get('max_value')}]"
        for c in constraints
    ]
    return ", ".join(rendered) if rendered else "(미명시)"


def _render_samples(samples: list[dict[str, Any]]) -> str:
    if not samples:
        return "(샘플 없음)"
    out = []
    for s in samples[:_MAX_SAMPLES]:
        inp = str(s.get("input_text", ""))[:_SAMPLE_FIELD_CHARS].replace("\n", " ")
        exp = str(s.get("expected_output", ""))[:_SAMPLE_FIELD_CHARS].replace("\n", " ")
        out.append(f"- in: {inp!r} | expected: {exp!r}")
    return "\n".join(out)


def _build_user_prompt(package: dict[str, Any], anchors: list[dict[str, Any]]) -> str:
    """패키지 → calibration user prompt (지문+제약+정해+샘플+내부힌트+anchor)."""
    problem = package.get("problem") or {}
    io = problem.get("io_contract") or {}
    solution = package.get("solution") or {}
    meta = package.get("meta") or {}
    code = str(solution.get("golden_code") or "(정해 없음)")[:_MAX_SOLUTION_CHARS]
    composition = meta.get("composition") or []
    return "\n".join(
        [
            "## 문제",
            str(problem.get("description", "")),
            "",
            "## 입출력 형식",
            f"입력: {io.get('input_format', '')}",
            f"출력: {io.get('output_format', '')}",
            "",
            "## 제약",
            _render_constraints(problem.get("constraints") or []),
            "",
            "## 정해 (golden solution)",
            f"```{solution.get('language', '')}",
            code,
            "```",
            "",
            "## 샘플",
            _render_samples(problem.get("sample_testcases") or []),
            "",
            "## 내부 힌트 (난이도 판단용, 응시자 비노출)",
            f"- 핵심 알고리즘: {meta.get('hidden_algorithm', '(미상)')}",
            f"- 합성 기법: {composition or '(없음)'}",
            "",
            _build_anchor_block(anchors),
        ]
    )


class DifficultyLLM(Protocol):
    """난이도 에이전트의 LLM dependency. test 가 mock 주입."""

    def evaluate(
        self, package: dict[str, Any], *, anchors: list[dict[str, Any]]
    ) -> DifficultyReport: ...


class AnthropicDifficultyLLM:
    """production impl — Sonnet + structured output(DifficultyReport), anchor 프롬프트."""

    def __init__(self, model: str = DIFFICULTY_MODEL) -> None:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.prompts import ChatPromptTemplate

        llm = ChatAnthropic(model_name=model, timeout=60, stop=None)
        prompt = ChatPromptTemplate.from_messages(
            [("system", _SYSTEM_PROMPT), ("user", "{user}")]
        )
        self._chain = (
            prompt | llm.with_structured_output(DifficultyReport)
        ).with_retry(stop_after_attempt=5, wait_exponential_jitter=True)

    def evaluate(
        self, package: dict[str, Any], *, anchors: list[dict[str, Any]]
    ) -> DifficultyReport:
        result = self._chain.invoke(
            {"user": _build_user_prompt(package, anchors)}
        )
        if not isinstance(result, DifficultyReport):
            msg = (
                f"with_structured_output 가 {type(result).__name__} 반환 — "
                "DifficultyReport 기대"
            )
            raise TypeError(msg)
        return result


def evaluate_difficulty(
    package: dict[str, Any],
    *,
    llm: DifficultyLLM,
    anchors: list[dict[str, Any]] | None = None,
) -> DifficultyReport:
    """패키지 → BOJ 티어 난이도 측정. anchors 기본은 ``load_anchors()``.

    인용 anchor id 는 로드된 anchor 집합으로 교집합 필터(LLM 환각 id 제거).
    """
    resolved = anchors if anchors is not None else load_anchors()
    report = llm.evaluate(package, anchors=resolved)
    known = {a.get("id") for a in resolved if isinstance(a.get("id"), str)}
    filtered = tuple(a for a in report.calibration_anchors if a in known)
    if filtered != report.calibration_anchors:
        report = report.model_copy(update={"calibration_anchors": filtered})
    return report


def annotate_difficulty(
    package: dict[str, Any],
    *,
    llm: DifficultyLLM,
    anchors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """``meta.difficulty`` 를 채운 **새 package** 를 불변 반환 (원본 무변경).

    빈 package(예: fail_verification — package 부재)는 무동작으로 그대로 반환한다.
    """
    if not package:
        return package
    report = evaluate_difficulty(package, llm=llm, anchors=anchors)
    meta = dict(package.get("meta") or {})
    meta["difficulty"] = report.model_dump()
    return {**package, "meta": meta}


# --------------------------------------------------------------------------- #
# 기존 DB 백필 — problems 행을 package shape 로 복원 → calibration → 컬럼 갱신       #
# --------------------------------------------------------------------------- #
def _row_to_package(row: Any) -> dict[str, Any]:
    """problems 행(RowMapping) → evaluate_difficulty 가 받는 package shape 복원.

    그래프/ingest 가 쓰는 package 와 동일 키 — 백필도 동일 함수를 재사용한다.
    """
    meta = row["internal_meta"] or {}
    return {
        "problem": {
            "title": row["title"],
            "description": row["description"],
            "io_contract": {
                "input_format": row["input_format"],
                "output_format": row["output_format"],
            },
            "constraints": row["constraints"] or [],
            "sample_testcases": row["samples"] or [],
        },
        "solution": {
            "golden_code": row["solution_code"],
            "language": row["solution_language"],
        },
        "meta": {
            "hidden_algorithm": meta.get("hidden_algorithm"),
            "composition": meta.get("composition") or [],
        },
    }


def backfill_difficulty(
    engine: Engine,
    *,
    llm: DifficultyLLM,
    anchors: list[dict[str, Any]] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> list[tuple[str, str]]:
    """난이도 미주석(``difficulty IS NULL``) problems 행을 calibration → (id, label).

    LLM 호출은 트랜잭션 밖에서, UPDATE 는 행별 짧은 트랜잭션으로 — 긴 락 회피.
    ``dry_run`` 이면 측정만 하고 write 하지 않는다. ``difficulty`` 컬럼(승격)과
    ``internal_meta.difficulty``(전체 report) 둘 다 갱신. ``force=True`` 면 이미
    난이도가 있는 행도 **재calibration**(anchor 확장/모델 교체 후 일관 재측정).
    """
    from sqlalchemy import select, update

    from ipe.v2.db.schema import problems

    with engine.connect() as conn:
        stmt = select(problems)
        if not force:
            stmt = stmt.where(problems.c.difficulty.is_(None))
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = list(conn.execute(stmt).mappings().all())

    done: list[tuple[str, str]] = []
    for row in rows:
        report = evaluate_difficulty(_row_to_package(row), llm=llm, anchors=anchors)
        done.append((row["id"], report.label))
        if dry_run:
            continue
        meta = dict(row["internal_meta"] or {})
        meta["difficulty"] = report.model_dump()
        with engine.begin() as conn:
            conn.execute(
                update(problems)
                .where(problems.c.id == row["id"])
                .values(difficulty=report.label, internal_meta=meta)
            )
    return done


def _build_parser() -> Any:
    import argparse
    import os

    p = argparse.ArgumentParser(
        prog="python -m ipe.v2.difficulty",
        description="기존 problems 행 난이도 백필 (RFC R4 calibration)",
    )
    p.add_argument("--db-url", default=os.environ.get("IPE_ADMIN_DB_URL"))
    p.add_argument("--limit", type=int, default=None, help="처리할 최대 행 수(선택)")
    p.add_argument(
        "--dry-run", action="store_true", help="측정만 — DB write 안 함(미리보기)"
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="이미 난이도가 있는 행도 재calibration (anchor 확장/모델 교체 후 일관 재측정)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    args = _build_parser().parse_args(argv)
    if not args.db_url:
        print("--db-url (또는 IPE_ADMIN_DB_URL) 필요")
        return 2
    from sqlalchemy import create_engine

    engine = create_engine(args.db_url, pool_pre_ping=True)
    done = backfill_difficulty(
        engine,
        llm=AnthropicDifficultyLLM(),
        limit=args.limit,
        dry_run=args.dry_run,
        force=args.force,
    )
    tag = "(dry-run)" if args.dry_run else ""
    for pid, label in done:
        print(f"  [{label:14}] {pid} {tag}")
    print(f"\n난이도 백필 {tag}: {len(done)} 행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
