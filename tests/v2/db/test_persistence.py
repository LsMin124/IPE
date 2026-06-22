"""persist_run 단위테스트 — sqlite(파일) 로 PG 없이 결정론 검증.

포터블 스키마(JSON variant) 덕에 sqlite 에서 적재 로직을 그대로 돌려 매핑·idempotency
를 확인한다. 운영 PG 통합은 별도(마크) 영역.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine

from ipe.v2.db import (
    generation_requests,
    init_schema,
    persist_run,
    problem_algorithms,
    problems,
    test_cases,
)


def _engine(tmp_path: Path) -> Engine:
    eng = create_engine(f"sqlite:///{tmp_path / 'bank.db'}")
    init_schema(eng)
    return eng


def _body(
    *,
    run_id: str,
    final_status: str = "success",
    with_package: bool = True,
    composition: list[str] | None = None,
) -> dict[str, Any]:
    package: dict[str, Any] | None = None
    if with_package:
        package = {
            "problem": {
                "title": "상수도 점검",
                "description": "지문 본문",
                "io_contract": {
                    "input_format": "첫 줄에 N, 다음 줄에 N개 정수",
                    "output_format": "정수 한 줄",
                    "example_separator": "newline",
                },
                "constraints": [{"name": "N", "min_value": 2, "max_value": 1000}],
                "sample_testcases": [{"input_text": "3\n1 2 3", "expected_output": "6"}],
            },
            "solution": {"golden_code": "print(sum(...))", "language": "python"},
            "test_suite": {
                "cases": [
                    {
                        "input_text": "3\n1 2 3",
                        "expected_output": "6",
                        "category": "small",
                        "golden_elapsed_ms": 40,
                    },
                    {
                        "input_text": "0\n",
                        "expected_output": "",
                        "category": "edge",
                        "golden_elapsed_ms": 10,
                    },
                ],
                "origin": "claude-opus-4-8",
            },
            "meta": {
                "package_version": "1.0",
                "mode": "p2",
                "hidden_algorithm": "dijkstra",
                "composition": (
                    ["union_find", "toposort"] if composition is None else composition
                ),
                "timing": {"max_golden_elapsed_ms": 40},
            },
        }
    return {
        "status": "completed",
        "final_status": final_status,
        "package": package,
        "cost_usd": 0.45,
        "batch": {
            "seed": "dijkstra",
            "run_index": 1,
            "run_id": run_id,
            "mode": "p2",
            "elapsed_s": 120.0,
        },
    }


def test_persist_success_maps_all_tables(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    pid = persist_run(eng, _body(run_id="r1"))

    assert pid is not None
    with eng.connect() as c:
        prob = c.execute(select(problems).where(problems.c.id == pid)).mappings().one()
        assert prob["title"] == "상수도 점검"
        assert prob["input_format"].startswith("첫 줄에 N")
        assert prob["solution_code"] == "print(sum(...))"
        assert prob["solution_language"] == "python"
        assert prob["status"] == "draft"
        assert prob["time_limit_ms"] == 1000  # max(1000, 40*3=120)
        assert prob["internal_meta"]["hidden_algorithm"] == "dijkstra"

        cases = (
            c.execute(
                select(test_cases)
                .where(test_cases.c.problem_id == pid)
                .order_by(test_cases.c.seq)
            )
            .mappings()
            .all()
        )
        assert len(cases) == 2
        assert cases[0]["expected"] == "6"
        assert cases[1]["expected"] == ""  # 퇴화 케이스 빈 expected 보존

        req = (
            c.execute(
                select(generation_requests).where(
                    generation_requests.c.idempotency_key == "r1"
                )
            )
            .mappings()
            .one()
        )
        assert req["final_status"] == "success"
        assert req["attempts"] == 1
        assert req["problem_id"] == pid
        assert req["raw_package"]["cost_usd"] == 0.45  # 원문 보관


def test_persist_idempotent_same_run_id(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    pid1 = persist_run(eng, _body(run_id="dup"))
    pid2 = persist_run(eng, _body(run_id="dup"))

    assert pid1 == pid2  # 재적재가 같은 문제 반환
    with eng.connect() as c:
        n_problems = c.execute(select(func.count()).select_from(problems)).scalar_one()
        assert n_problems == 1  # 중복 생성 안 함
        attempts = c.execute(
            select(generation_requests.c.attempts).where(
                generation_requests.c.idempotency_key == "dup"
            )
        ).scalar_one()
        assert attempts == 2  # 재시도 카운트 증가


def test_persist_without_package_logs_only(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    pid = persist_run(
        eng, _body(run_id="f1", final_status="fail_verification", with_package=False)
    )

    assert pid is None  # 문제 미적재
    with eng.connect() as c:
        n_problems = c.execute(select(func.count()).select_from(problems)).scalar_one()
        assert n_problems == 0
        req = (
            c.execute(
                select(generation_requests).where(
                    generation_requests.c.idempotency_key == "f1"
                )
            )
            .mappings()
            .one()
        )
        assert req["final_status"] == "fail_verification"
        assert req["problem_id"] is None  # 감사 로그만


def test_persist_writes_algorithm_junction(tmp_path: Path) -> None:
    """problem_algorithms: 코어(role='core') + 합성(role='composition') N:M 적재."""
    eng = _engine(tmp_path)
    pid = persist_run(eng, _body(run_id="algo"))  # composition=[union_find, toposort]

    assert pid is not None
    with eng.connect() as c:
        rows = (
            c.execute(
                select(problem_algorithms).where(
                    problem_algorithms.c.problem_id == pid
                )
            )
            .mappings()
            .all()
        )
    pairs = {(r["algorithm"], r["role"]) for r in rows}
    assert ("dijkstra", "core") in pairs  # 코어 = hidden_algorithm
    assert ("union_find", "composition") in pairs
    assert ("toposort", "composition") in pairs
    assert len(rows) == 3  # 코어 1 + 합성 2


def test_persist_algorithm_junction_core_only_when_no_composition(
    tmp_path: Path,
) -> None:
    """P1(합성 없음): 코어 1행만 적재된다."""
    eng = _engine(tmp_path)
    pid = persist_run(eng, _body(run_id="p1", composition=[]))

    assert pid is not None
    with eng.connect() as c:
        rows = (
            c.execute(
                select(problem_algorithms).where(
                    problem_algorithms.c.problem_id == pid
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1
    assert rows[0]["algorithm"] == "dijkstra"
    assert rows[0]["role"] == "core"
