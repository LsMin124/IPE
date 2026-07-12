"""SampleExplanations — 예제 설명(sample explanation) LLM 의 structured output.

BOJ 표준 '예제 설명' 채널의 typed 계약. ``SampleTestCase.description`` 은 스키마에
있으나 어떤 노드도 채우지 않아 출하 문제의 예제 설명이 영구 빈 문자열이었다 —
v2 ``sample_explainer`` 노드가 sample_filler(golden 실행으로 expected 확정) 직후
이 모델로 설명을 받아 각 sample.description 에 스탬프한다. 설명은 '이 입력에서
왜 이 출력이 나오는지' **인스턴스 수준** 사실만(해법 은닉 규율은 노드 프롬프트가
집행), 순서는 샘플 순서 그대로.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SampleExplanations(BaseModel):
    """sample_explainer LLM 출력 — 샘플 순서 그대로의 예제 설명 텍스트 목록.

    개수는 샘플 개수와 정확히 같아야 한다(프롬프트 규율). 불일치 시 노드가 min
    길이까지만 채우고 나머지 sample 은 원본 유지 — 장식적 품질 채널이라 어떤
    경우에도 파이프라인을 죽이지 않는다(방어적 완화, 노드 docstring 참조).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    explanations: list[str] = Field(
        ...,
        description="샘플 순서 그대로의 예제 설명 (샘플당 1~3문장 한국어, 인스턴스 수준)",
    )
