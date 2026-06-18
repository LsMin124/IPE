"""v2 노드 system prompt 의 ChatPromptTemplate 변수 무결성 (GD-run1 실측 회귀).

#141 이 spec_bridge prompt 에 넣은 JSON 예시의 중괄호를 ChatPromptTemplate 이
템플릿 변수로 해석 → production chain 이 invoke 마다 KeyError(재시도 전멸 →
fail_spec_authoring). mock 주입 단위 테스트는 production 템플릿 생성을 우회해
못 잡는 빈틈 — 모든 노드 prompt 를 **실제 템플릿으로 구성**해 입력 변수가
{'user'} 뿐임을 게이트로 강제한다 (prompt 에 literal 중괄호가 필요하면
``{{ }}`` 로 이스케이프).
"""

from __future__ import annotations

import importlib

import pytest
from langchain_core.prompts import ChatPromptTemplate

# strategist 는 composition_mode 별 함수(_system_prompt) — 아래 전용 테스트로 분리.
_PROMPT_MODULES = [
    "ipe.v2.nodes.formalizer",
    "ipe.v2.nodes.narrative",
    "ipe.v2.nodes.faithfulness",
    "ipe.v2.nodes.spec_bridge",
    "ipe.v2.nodes.generator_designer",
]


@pytest.mark.parametrize("module_name", _PROMPT_MODULES)
def test_system_prompt_has_no_stray_template_variables(module_name: str) -> None:
    """production 과 동일하게 템플릿 구성 — 미지의 입력 변수가 생기면 invoke 가
    KeyError 로 전멸하므로 {'user'} 외 변수는 곧 prompt 버그다."""
    mod = importlib.import_module(module_name)
    template = ChatPromptTemplate.from_messages(
        [("system", mod._SYSTEM_PROMPT), ("user", "{user}")]
    )
    assert set(template.input_variables) == {"user"}, sorted(
        template.input_variables
    )


def test_strategist_system_prompt_has_no_stray_template_variables() -> None:
    """strategist 는 composition_mode 별 함수 — single/composed 양 prompt 모두
    {'user'} 외 변수가 없어야 한다 (f-string 치환 후 literal 중괄호 잔존 차단)."""
    from ipe.v2.nodes.strategist import _system_prompt

    for mode in ("single", "composed"):
        template = ChatPromptTemplate.from_messages(
            [("system", _system_prompt(mode)), ("user", "{user}")]  # type: ignore[arg-type]
        )
        assert set(template.input_variables) == {"user"}, (
            mode,
            sorted(template.input_variables),
        )


def test_coder_parse_discipline_prompt_has_no_stray_variables() -> None:
    """v2 synthesis 코더 prompt(parse_discipline on/off)도 production 템플릿 무결성 —
    파싱 규율 텍스트에 literal 중괄호가 섞이면 invoke 가 KeyError 로 전멸한다."""
    from ipe.v1.nodes.coder import _coder_system_prompt

    for flag in (True, False):
        template = ChatPromptTemplate.from_messages(
            [("system", _coder_system_prompt(flag)), ("user", "{user}")]
        )
        assert set(template.input_variables) == {"user"}, (flag, sorted(
            template.input_variables
        ))


def test_qa_reviewer_rendered_prompts_have_no_stray_variables() -> None:
    """qa_reviewer 는 kind 별 charter 를 Python .format 으로 선렌더 — 렌더 결과가
    템플릿을 통과할 때도 동일 무결성이 성립해야 한다."""
    from ipe.v2.nodes.qa_reviewer import _CHARTERS, _SYSTEM_PROMPT_TEMPLATE

    for kind, charter in _CHARTERS.items():
        system = _SYSTEM_PROMPT_TEMPLATE.format(charter=charter, kind=kind)
        template = ChatPromptTemplate.from_messages(
            [("system", system), ("user", "{user}")]
        )
        assert set(template.input_variables) == {"user"}, (
            kind,
            sorted(template.input_variables),
        )
