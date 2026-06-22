"""DB 영속화 레이어 — 파이프라인이 생성 패키지를 공유 DB 에 적재한다.

운영 토폴로지: 파이프라인 서버(생성) → **공유 PostgreSQL**(write) → 서비스 백엔드(read).
계약 §0 경계 변경(기존: 무상태/백엔드 영속화 전담 → 변경: 파이프라인이 직접 적재).
파이프라인이 DDL/마이그레이션을 소유한다 (alembic).

스키마는 sqlite(단위테스트)·PostgreSQL(운영) 양쪽 포터블 — JSON 컬럼은 PG 에서 jsonb.
"""

from .persistence import init_schema, persist_run
from .schema import (
    generation_requests,
    metadata,
    problem_algorithms,
    problems,
    test_cases,
)

__all__ = [
    "generation_requests",
    "init_schema",
    "metadata",
    "persist_run",
    "problem_algorithms",
    "problems",
    "test_cases",
]
