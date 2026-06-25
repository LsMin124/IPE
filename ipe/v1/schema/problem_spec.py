"""ProblemSpec — Architect 출력의 typed contract.

기존 (v0): ``ProblemState`` 에 prose 로 흩어져 있음 (``problem_title``,
``problem_description``, ``constraints``, ``constraints_structured``,
``sample_testcases``, ``has_special_judge``, ``special_judge_code``).

v1: 단일 immutable Pydantic 모델. 노드 간 통신을 typed structured artifacts 로
전환 (D안 H1 — information bottleneck 가설 검증).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TargetAlgorithm(StrEnum):
    """v1 graph 가 지원하는 algorithm.

    Phase 1 = Dijkstra MVR. Phase 2a = + LIS / Segment Tree / Two Sum / BFS
    (baseline 5 완성). free str 대신 enum 으로 좁혀서 symbolic verifier
    dispatch (D안 H2) 의 silent fallback 회피.
    """

    DIJKSTRA = "dijkstra"
    LIS = "lis"
    SEGTREE = "segtree"
    TWO_SUM = "two_sum"
    BFS = "bfs"
    BINARY_SEARCH = "binary_search"
    UNION_FIND = "union_find"
    TOPOSORT = "toposort"
    KNAPSACK = "knapsack"
    SORT = "sort"
    STRING_MATCH = "string_match"
    MAX_FLOW = "max_flow"
    SIEVE = "sieve"
    BELLMAN_FORD = "bellman_ford"
    FLOYD_WARSHALL = "floyd_warshall"
    KRUSKAL_MST = "kruskal_mst"
    HEAP = "heap"
    FENWICK = "fenwick"
    COIN_CHANGE = "coin_change"
    # 초급 카테고리 (easy track) — 알고리즘이 아니라 기초 스킬. is_basic() 으로 분류,
    # strategist/formalizer 가 은닉/위장 대신 명확·직접 저작으로 분기. 난이도는 별도
    # 입력이 아니라 seed 에서 파생(단일소스). symbolic verifier 는 None-dispatch
    # (golden/brute reconcile 가 검증). 입력은 스칼라/소형 배열이라 NullBackbone.
    BASIC_IO = "basic_io"
    ARITHMETIC = "arithmetic"
    CONDITIONAL = "conditional"
    LOOP_ACCUMULATE = "loop_accumulate"


# 초급 카테고리 단일소스 — easy 저작 분기의 진실. 별도 difficulty 입력 없이 seed 가
# 난이도를 말한다(모순 불가: "easy dijkstra"/"hard basic_io" 상태가 안 생김).
_BASIC_TARGETS: frozenset[TargetAlgorithm] = frozenset(
    {
        TargetAlgorithm.BASIC_IO,
        TargetAlgorithm.ARITHMETIC,
        TargetAlgorithm.CONDITIONAL,
        TargetAlgorithm.LOOP_ACCUMULATE,
    }
)


def is_basic(target: TargetAlgorithm) -> bool:
    """초급 카테고리(기초 스킬)인가 — easy 저작 분기 신호.

    True 면 strategist/formalizer 가 은닉/위장 대신 명확·직접 서술 + 작은 입력으로
    저작한다. 난이도를 별도 입력으로 받지 않고 seed 에서 파생(단일소스·모순 불가).
    """
    return target in _BASIC_TARGETS


class ConstraintRange(BaseModel):
    """변수의 값 범위. ``min_value``/``max_value`` 둘 다 inclusive."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1, description="변수 이름 (예: 'N', 'V', 'E')")
    min_value: int = Field(..., description="최소값 (inclusive)")
    max_value: int = Field(..., description="최대값 (inclusive)")
    symbolic_max: str | None = Field(
        default=None,
        description=(
            "데이터 의존 상한의 **기호 표현** (예: 참조 스칼라 origin 의 상한 'V'). "
            "설정 시 constraints 텍스트가 max_value(정적 최대) 대신 이 기호로 렌더된다 — "
            "input_format 의 '크기 이하' 서술과 정합(정적 [1, V상한] vs 기호 '≤V' 모순 해소). "
            "None 이면 max_value 숫자 사용(현행). max_value 는 numeric fallback 으로 보존."
        ),
    )
    description: str = ""

    @model_validator(mode="after")
    def _check_min_le_max(self) -> ConstraintRange:
        if self.min_value > self.max_value:
            msg = (
                f"ConstraintRange '{self.name}': "
                f"min_value ({self.min_value}) > max_value ({self.max_value})"
            )
            raise ValueError(msg)
        return self


class IOContract(BaseModel):
    """문제의 입출력 형식 명세. Coder 의 parse/print 코드 anchor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_format: str = Field(..., min_length=1)
    output_format: str = Field(..., min_length=1)
    example_separator: Literal["newline", "space", "custom"] = "newline"


class SampleTestCase(BaseModel):
    """샘플 입출력. Phase A exact match anchor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_text: str
    expected_output: str
    description: str = ""


class ProblemSpec(BaseModel):
    """Architect → Designer/Coder 가 의존하는 typed problem contract.

    v1 핵심: 자연어 prose 는 ``description`` 한 필드에만. 나머지는 structured.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_algorithm: TargetAlgorithm
    title: str = Field(..., min_length=1)
    description: str = Field(
        ..., min_length=1, description="사람용 자연어 설명. LLM 은 structured 필드 우선"
    )
    constraints: list[ConstraintRange] = Field(default_factory=list)
    io_contract: IOContract
    sample_testcases: list[SampleTestCase] = Field(..., min_length=3, max_length=5)
    time_limit_ms: int = Field(default=2000, gt=0)
    memory_limit_mb: int = Field(default=256, gt=0)
    input_parser_code: str = Field(
        default="",
        description=(
            "결정적 canonical stdin 파서 preamble (node 가 io_schema 에서 코드 렌더해 "
            "채움 — LLM 은 비워둘 것). v2 synthesis 코더가 파서 분산 없이 알고리즘만 "
            "작성하게 하는 anchor. v1 canonical mode 는 빈 문자열(=미주입)."
        ),
    )
