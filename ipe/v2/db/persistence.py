"""패키지 → DB 적재 — 배치/API body(§2.5 package) 를 정규화 3 테이블에 write.

``persist_run`` 은 **idempotent**: 같은 ``idempotency_key``(배치 run_id) 재적재 시 문제를
중복 생성하지 않고 attempts 만 증가시킨다 (워커 재시작·--retry-failed 안전).
``package`` 가 없으면(fail_verification 등) 감사 로그(generation_requests)만 남긴다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection, Engine

from .schema import (
    generation_requests,
    metadata,
    problem_algorithms,
    problems,
    test_cases,
)

# TL 산정 (계약 §4): time_limit_ms = max(하한, max_golden_elapsed_ms × 배수).
_TL_MULTIPLIER = 3
_TL_FLOOR_MS = 1000


def init_schema(engine: Engine) -> None:
    """테이블이 없으면 생성 (idempotent). 운영은 alembic, 테스트·부트스트랩은 이걸로."""
    metadata.create_all(engine)


def _time_limit_ms(package: dict[str, Any]) -> int | None:
    timing = (package.get("meta") or {}).get("timing") or {}
    max_ms = timing.get("max_golden_elapsed_ms")
    if max_ms is None:
        return None
    return max(_TL_FLOOR_MS, round(float(max_ms) * _TL_MULTIPLIER))


def _insert_problem_algorithms(
    conn: Connection, problem_id: str, meta: dict[str, Any]
) -> None:
    """알고리즘 분류 N:M 적재 — 코어(role='core') + 합성(role='composition').

    둘 다 ``internal_meta`` 출처(hidden_algorithm / composition, TargetAlgorithm 어휘).
    코어가 합성에도 들어가 있으면 중복 PK 회피로 코어 우선 1회만.
    """
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    core = meta.get("hidden_algorithm")
    if core:
        rows.append({"problem_id": problem_id, "algorithm": core, "role": "core"})
        seen.add(core)
    for tech in meta.get("composition") or []:
        if tech and tech not in seen:
            rows.append(
                {"problem_id": problem_id, "algorithm": tech, "role": "composition"}
            )
            seen.add(tech)
    if rows:
        conn.execute(insert(problem_algorithms), rows)


def _insert_problem(conn: Connection, package: dict[str, Any], now: datetime) -> str:
    problem = package["problem"]
    io_contract = problem.get("io_contract") or {}
    solution = package.get("solution") or {}
    meta = package.get("meta") or {}
    problem_id = str(uuid.uuid4())
    conn.execute(
        insert(problems).values(
            id=problem_id,
            title=problem.get("title", ""),
            description=problem.get("description", ""),
            input_format=io_contract.get("input_format", ""),
            output_format=io_contract.get("output_format", ""),
            constraints=problem.get("constraints", []),
            samples=problem.get("sample_testcases", []),
            internal_meta=meta,
            # difficulty: meta.difficulty.label (사후 calibration, RFC R4) 승격. 미주석이면 None.
            difficulty=(meta.get("difficulty") or {}).get("label"),
            solution_code=solution.get("golden_code"),
            solution_language=solution.get("language"),
            status="draft",
            time_limit_ms=_time_limit_ms(package),
            created_at=now,
        )
    )
    cases = (package.get("test_suite") or {}).get("cases") or []
    if cases:
        conn.execute(
            insert(test_cases),
            [
                {
                    "problem_id": problem_id,
                    "seq": i,
                    "input": c.get("input_text", ""),
                    "expected": c.get("expected_output", ""),
                    "category": c.get("category"),
                }
                for i, c in enumerate(cases)
            ],
        )
    _insert_problem_algorithms(conn, problem_id, meta)
    return problem_id


def persist_run(
    engine: Engine, body: dict[str, Any], *, now: datetime | None = None
) -> str | None:
    """배치/API run body 를 DB 에 적재. 새 문제면 problem_id, 아니면(미적재) None.

    idempotent: idempotency_key(=batch.run_id) 가 이미 있으면 attempts 만 증가시키고
    기존 problem_id 를 반환 (재insert 안 함).
    """
    now = now or datetime.now(UTC)
    batch = body.get("batch") or {}
    idem = batch.get("run_id") or uuid.uuid4().hex
    with engine.begin() as conn:
        existing = conn.execute(
            select(
                generation_requests.c.problem_id, generation_requests.c.attempts
            ).where(generation_requests.c.idempotency_key == idem)
        ).first()
        if existing is not None:
            prev_problem_id, prev_attempts = existing[0], existing[1]
            conn.execute(
                update(generation_requests)
                .where(generation_requests.c.idempotency_key == idem)
                .values(attempts=prev_attempts + 1, final_status=body["final_status"])
            )
            return prev_problem_id  # type: ignore[no-any-return]
        package = body.get("package")
        problem_id = (
            _insert_problem(conn, package, now) if package is not None else None
        )
        conn.execute(
            insert(generation_requests).values(
                idempotency_key=idem,
                seed=batch.get("seed", ""),
                mode=batch.get("mode", ""),
                job_id=batch.get("run_id"),
                final_status=body["final_status"],
                attempts=1,
                raw_package=body,
                problem_id=problem_id,
                created_at=now,
            )
        )
        return problem_id
