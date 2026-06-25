"""IPE v1 schema — typed structured artifacts (Pydantic v2).

v0 ``ProblemState`` (TypedDict prose) 의 후속. D안 Phase 1 PR-A1 산출.
"""

from __future__ import annotations

from .algorithm_design import AlgorithmDesign, ComplexityBound, EdgeCase, Invariant
from .blueprint import (
    BlueprintFormalization,
    GraphShape,
    IOFieldSpec,
    IOSchema,
    IRValidationReport,
    Narrative,
    NarrativeDraft,
    NarrativeFaithfulnessReport,
    OutputInvariant,
    ProblemBlueprint,
    SequenceShape,
    StrategySeed,
    StringShape,
)
from .difficulty import DifficultyFactors, DifficultyReport, DifficultyTier
from .iteration_context import FailedStrategy, IterationContext, IterationRecord
from .problem_spec import (
    ConstraintRange,
    IOContract,
    ProblemSpec,
    SampleTestCase,
    TargetAlgorithm,
)
from .qa import QAFinding, QAReport, QAReview, QAReviewerKind, QASeverity
from .solution_attempt import Lesson, SolutionAttempt
from .synthesis import ReconciliationResult, SolutionCandidate
from .test_suite import (
    EdgeCaseSpec,
    GeneratedTestCase,
    GeneratorContract,
    ResolvedEdgeCase,
    ScaleFamily,
    TestSuite,
)
from .verification_result import (
    FailureMode,
    InvariantViolation,
    SampleResult,
    StructuredFeedback,
    TargetNode,
    VerificationResult,
)

__all__ = [
    "AlgorithmDesign",
    "BlueprintFormalization",
    "ComplexityBound",
    "ConstraintRange",
    "DifficultyFactors",
    "DifficultyReport",
    "DifficultyTier",
    "EdgeCase",
    "EdgeCaseSpec",
    "FailedStrategy",
    "FailureMode",
    "GeneratedTestCase",
    "GeneratorContract",
    "GraphShape",
    "IOContract",
    "IOFieldSpec",
    "IOSchema",
    "IRValidationReport",
    "Invariant",
    "InvariantViolation",
    "IterationContext",
    "IterationRecord",
    "Lesson",
    "Narrative",
    "NarrativeDraft",
    "NarrativeFaithfulnessReport",
    "OutputInvariant",
    "ProblemBlueprint",
    "ProblemSpec",
    "QAFinding",
    "QAReport",
    "QAReview",
    "QAReviewerKind",
    "QASeverity",
    "ReconciliationResult",
    "ResolvedEdgeCase",
    "SampleResult",
    "SampleTestCase",
    "ScaleFamily",
    "SequenceShape",
    "SolutionAttempt",
    "SolutionCandidate",
    "StrategySeed",
    "StringShape",
    "StructuredFeedback",
    "TargetAlgorithm",
    "TargetNode",
    "TestSuite",
    "VerificationResult",
]
