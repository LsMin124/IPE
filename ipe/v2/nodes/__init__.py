"""V2 graph 노드 — blueprint-first B2B 파이프라인 (Phase 3 M3+).

각 노드는 ``V2State → V2State`` factory pattern (v1 nodes 와 동일). LLM 의존성은
Protocol 로 정의, production impl 은 langchain-anthropic ``with_structured_output``
사용. modeling layer(M3 step2)는 책임 분리(Q1): Strategist(Sonnet, 발산 시드) →
Formalizer(Opus, blueprint freeze).
"""

from __future__ import annotations

from .edge_filler import make_edge_filler_node
from .faithfulness import (
    AnthropicFaithfulnessLLM,
    FaithfulnessLLM,
    make_faithfulness_node,
)
from .formalizer import (
    AnthropicFormalizerLLM,
    FormalizerLLM,
    make_formalizer_node,
)
from .generator_designer import make_generator_designer_node
from .input_generator import make_input_generator_node
from .narrative import (
    AnthropicNarrativeLLM,
    NarrativeLLM,
    make_narrative_node,
)
from .qa_aggregator import make_qa_aggregator_node
from .qa_reviewer import (
    AnthropicQAReviewerLLM,
    QAReviewerLLM,
    make_qa_reviewer_node,
)
from .reconciler import make_v2_reconciler_node
from .sample_explainer import (
    AnthropicSampleExplainerLLM,
    SampleExplainerLLM,
    make_sample_explainer_node,
)
from .sample_filler import make_sample_filler_node
from .spec_bridge import make_spec_bridge_node
from .spec_patch import make_spec_patch_node
from .strategist import (
    AnthropicStrategistLLM,
    StrategistLLM,
    make_strategist_node,
)
from .suite_assembler import make_suite_assembler_node
from .validator import make_validator_node, validate_ir

__all__ = [
    "AnthropicFaithfulnessLLM",
    "AnthropicFormalizerLLM",
    "AnthropicNarrativeLLM",
    "AnthropicQAReviewerLLM",
    "AnthropicSampleExplainerLLM",
    "AnthropicStrategistLLM",
    "FaithfulnessLLM",
    "FormalizerLLM",
    "NarrativeLLM",
    "QAReviewerLLM",
    "SampleExplainerLLM",
    "StrategistLLM",
    "make_edge_filler_node",
    "make_faithfulness_node",
    "make_formalizer_node",
    "make_generator_designer_node",
    "make_input_generator_node",
    "make_narrative_node",
    "make_qa_aggregator_node",
    "make_qa_reviewer_node",
    "make_sample_explainer_node",
    "make_sample_filler_node",
    "make_spec_bridge_node",
    "make_spec_patch_node",
    "make_strategist_node",
    "make_suite_assembler_node",
    "make_v2_reconciler_node",
    "make_validator_node",
    "validate_ir",
]
