"""QA/Critic 스테이지 아티팩트 (Phase 3 M5 — RFC N10/N11).

suite 까지 완성된 문제 패키지(narrative+spec+test_suite)를 5 관점의 병렬 QA 리뷰어
(N10a-e: 모호성/공정성/유출/난이도/지문품질)가 검토하고, deterministic aggregator(N11)가
집계해 출하 게이트를 친다.

artifacts:
- ``QAFinding``: 한 건의 지적 (severity: info < warning < blocker).
- ``QAReview``: 리뷰어 1종의 판정 — kind + passed + findings. **passed=True 인데
  blocker finding 이 있으면 모순** → validator reject (LLM 산출 일관성 강제).
- ``QAReport``: aggregator 집계 — ``overall_pass``(전원 통과) + ``failed_kinds``.

유출(leakage) 리뷰어는 LLM 의 유명 문제 동형성 판단으로 시작 — 외부 reference
corpus 조회는 별도 과제로 이연 (RFC Q2, M5 진입 시 결정).

모두 frozen + extra=forbid (schema 컨벤션). additive 신규 — 기존 스키마 무변경.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# presentation(지문 품질 — 문체·구성)은 상용 저지 수준 작문 게이트 (N10e, additive).
QAReviewerKind = Literal[
    "ambiguity", "fairness", "leakage", "difficulty", "presentation"
]
QASeverity = Literal["info", "warning", "blocker"]


class QAFinding(BaseModel):
    """리뷰어가 남긴 한 건의 지적 — 심각도 + 설명."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: QASeverity
    description: str = Field(..., min_length=1)


class QAReview(BaseModel):
    """QA 리뷰어 1종(N10a-e)의 판정.

    ``passed=True`` 와 blocker finding 의 공존은 모순 — LLM 이 산출한 판정의
    내적 일관성을 schema 가 강제한다 (왜곡된 구조화 출력 조기 reject).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: QAReviewerKind
    passed: bool
    findings: tuple[QAFinding, ...] = ()
    rationale: str = ""

    @model_validator(mode="after")
    def _passed_has_no_blocker(self) -> QAReview:
        if self.passed and any(f.severity == "blocker" for f in self.findings):
            msg = "passed=True 인데 blocker finding 존재 — 모순된 리뷰"
            raise ValueError(msg)
        return self


class QAReport(BaseModel):
    """QA aggregator(N11) 집계 — 출하 게이트의 근거."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reviews: tuple[QAReview, ...] = Field(..., min_length=1)

    @property
    def overall_pass(self) -> bool:
        """모든 리뷰어 통과 시에만 출하 가능."""
        return all(r.passed for r in self.reviews)

    @property
    def failed_kinds(self) -> tuple[QAReviewerKind, ...]:
        """실패한 리뷰어 kind 들 — back-route 대상 결정의 근거 (후속 step)."""
        return tuple(r.kind for r in self.reviews if not r.passed)
