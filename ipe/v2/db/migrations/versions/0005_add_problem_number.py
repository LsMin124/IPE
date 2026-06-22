"""add problems.problem_number — 공개 검색 번호(1000~) + 기존 행 백필

Revision ID: 0005_problem_number
Revises: 0004_algorithm_junction
Create Date: 2026-06-22

UUID(`problems.id`) 와 별개로 사람이 검색/노출에 쓰는 공개 정수 번호(BOJ 문제번호 격).
적재 시 파이프라인이 채번(base 1000). 기존 행은 ``created_at`` 순서로 1000부터 백필한 뒤
NOT NULL + UNIQUE 를 건다. 계약 v3.1 (additive).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_problem_number"
down_revision: str | None = "0004_algorithm_junction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) nullable 로 추가 (기존 행 무중단).
    op.add_column(
        "problems", sa.Column("problem_number", sa.BigInteger(), nullable=True)
    )
    # 2) 백필 — created_at 순 1000부터 (PostgreSQL). 1000=가장 오래된 문제.
    op.execute(
        "WITH numbered AS ("
        "  SELECT id, 999 + ROW_NUMBER() OVER (ORDER BY created_at, id) AS n "
        "  FROM problems"
        ") "
        "UPDATE problems p SET problem_number = numbered.n "
        "FROM numbered WHERE p.id = numbered.id"
    )
    # 3) NOT NULL + UNIQUE 확정 (채번 보장 후).
    op.alter_column("problems", "problem_number", nullable=False)
    op.create_unique_constraint(
        "uq_problems_problem_number", "problems", ["problem_number"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_problems_problem_number", "problems", type_="unique")
    op.drop_column("problems", "problem_number")
