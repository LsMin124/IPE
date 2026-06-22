"""replace problems.algorithm scalar → problem_algorithms (N:M 정션)

Revision ID: 0004_algorithm_junction
Revises: 0003_add_difficulty
Create Date: 2026-06-22

알고리즘 분류를 단일 스칼라(코어만)에서 N:M 정션으로 교체 — 합성 기법(composition)
까지 필터 가능하도록(계약 v3.0, breaking). 코어(reduction_core)=role 'core', 합성
기법=role 'composition'. 둘 다 ``internal_meta``(hidden_algorithm / composition, jsonb)
에서 백필 후 구 ``problems.algorithm`` 스칼라 컬럼을 제거한다.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_algorithm_junction"
down_revision: str | None = "0003_add_difficulty"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "problem_algorithms",
        sa.Column(
            "problem_id",
            sa.String(36),
            sa.ForeignKey("problems.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("algorithm", sa.String(64), nullable=False),  # TargetAlgorithm 값
        sa.Column("role", sa.String(16), nullable=False),  # 'core' | 'composition'
        sa.PrimaryKeyConstraint("problem_id", "algorithm"),
    )
    op.create_index(
        "ix_problem_algorithms_algorithm", "problem_algorithms", ["algorithm"]
    )
    # 백필 — 코어: internal_meta(jsonb).hidden_algorithm (PostgreSQL).
    op.execute(
        "INSERT INTO problem_algorithms (problem_id, algorithm, role) "
        "SELECT id, internal_meta->>'hidden_algorithm', 'core' FROM problems "
        "WHERE internal_meta->>'hidden_algorithm' IS NOT NULL"
    )
    # 백필 — 합성: internal_meta.composition (jsonb array). 코어와 중복이면 skip.
    op.execute(
        "INSERT INTO problem_algorithms (problem_id, algorithm, role) "
        "SELECT id, jsonb_array_elements_text(internal_meta->'composition'), "
        "'composition' FROM problems "
        "WHERE jsonb_typeof(internal_meta->'composition') = 'array' "
        "ON CONFLICT (problem_id, algorithm) DO NOTHING"
    )
    # 구 스칼라 컬럼 제거 — 정션이 대체.
    op.drop_index("ix_problems_algorithm", table_name="problems")
    op.drop_column("problems", "algorithm")


def downgrade() -> None:
    op.add_column("problems", sa.Column("algorithm", sa.String(64), nullable=True))
    op.create_index("ix_problems_algorithm", "problems", ["algorithm"])
    # 코어(role='core')만 스칼라로 복원 — 합성 정보는 internal_meta 에 잔존.
    op.execute(
        "UPDATE problems SET algorithm = ("
        "SELECT pa.algorithm FROM problem_algorithms pa "
        "WHERE pa.problem_id = problems.id AND pa.role = 'core' LIMIT 1)"
    )
    op.drop_index("ix_problem_algorithms_algorithm", table_name="problem_algorithms")
    op.drop_table("problem_algorithms")
