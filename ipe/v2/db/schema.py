"""DB 스키마 SSOT — 정규화 4 테이블 (계약 §3, 파이프라인 소유 DDL).

JSON 컬럼은 ``JSON().with_variant(JSONB, "postgresql")`` 로 포터블: 운영 PostgreSQL
에서는 jsonb, 단위테스트 sqlite 에서는 일반 JSON(TEXT) 로 매핑된다.

- ``problems``: 출하 문제 1건 (지문+io+제약+샘플+정해+내부메타+TL). status 는
  ``draft``/``review``/``published`` — 파이프라인은 ``draft`` 로 적재, 승격은 백엔드.
- ``test_cases``: 채점셋 케이스 (problem_id FK, 줄단위 exact-match 채점용).
- ``problem_algorithms``: 문제 ↔ 알고리즘 N:M (코어+합성, role 구분 — 백엔드 분류 필터).
- ``generation_requests``: 생성 요청 감사 로그 (idempotency_key PK, raw_package 원문).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

# PG=jsonb / sqlite 등=JSON — 단위테스트(sqlite)와 운영(PG) 양쪽 동작.
_JSON = JSON().with_variant(JSONB, "postgresql")

metadata = MetaData()

problems = Table(
    "problems",
    metadata,
    Column("id", String(36), primary_key=True),  # uuid4 hex-dash (내부 안정 식별)
    # problem_number: 공개 검색용 정수 번호(1000~, 적재 시 파이프라인 채번). UUID 와 별개로
    # 사람이 검색/노출에 쓰는 핸들(BOJ 문제번호 격). ⚠️ 노출 가능(내부 컬럼 아님).
    Column("problem_number", BigInteger, nullable=False, unique=True),
    Column("title", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("input_format", Text, nullable=False),
    Column("output_format", Text, nullable=False),
    Column("constraints", _JSON, nullable=False),  # [{name,min_value,max_value,...}]
    Column("samples", _JSON, nullable=False),  # [{input_text,expected_output,...}]
    # internal_meta: hidden_algorithm/composition/qa 등 — 응시자 비노출 (계약 §2.5)
    Column("internal_meta", _JSON, nullable=False),
    # difficulty: BOJ 티어 라벨(예: 'Gold IV'). meta.difficulty.label 에서 승격 — 쿼리·
    # 필터·집계 편의로 1급 컬럼(승격 패턴). 사후 calibration(RFC R4) 산출,
    # 응시자 비노출(internal). 전체 report 는 internal_meta.difficulty 에 보존.
    Column("difficulty", String(64), nullable=True, index=True),
    Column("solution_code", Text, nullable=True),  # 내부 정해 (응시자 비노출)
    Column("solution_language", String(16), nullable=True),
    Column("status", String(16), nullable=False, default="draft"),
    Column("time_limit_ms", Integer, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

test_cases = Table(
    "test_cases",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "problem_id",
        String(36),
        ForeignKey("problems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("seq", Integer, nullable=False),
    Column("input", Text, nullable=False),
    Column("expected", Text, nullable=False),  # "" 가능(퇴화 케이스 — 정해 출력 자체가 빈값)
    Column("category", String(64), nullable=True),
)

# 문제 ↔ 알고리즘 분류 N:M (계약 v3.0). 코어(reduction_core)=role 'core' 1행 +
# 합성(composition)=role 'composition' N행. 둘 다 TargetAlgorithm(19종) 어휘.
# 백엔드 알고리즘 필터링용 — 구 problems.algorithm 스칼라(코어만)를 대체. 응시자 비노출.
problem_algorithms = Table(
    "problem_algorithms",
    metadata,
    Column(
        "problem_id",
        String(36),
        ForeignKey("problems.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("algorithm", String(64), primary_key=True, index=True),  # TargetAlgorithm 값
    Column("role", String(16), nullable=False),  # 'core' | 'composition'
)

generation_requests = Table(
    "generation_requests",
    metadata,
    Column("idempotency_key", String(64), primary_key=True),
    Column("seed", String(64), nullable=False),
    Column("mode", String(16), nullable=False),
    Column("job_id", String(64), nullable=True),
    Column("final_status", String(32), nullable=False),
    Column("attempts", Integer, nullable=False, default=1),
    Column("raw_package", _JSON, nullable=True),  # 수신 패키지 원문(감사/재적재)
    Column(
        "problem_id",
        String(36),
        ForeignKey("problems.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
