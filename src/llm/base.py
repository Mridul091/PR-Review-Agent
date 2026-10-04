from abc import ABC, abstractmethod
from typing import Annotated, Literal

from pydantic import Field

from src.review.schemas import (
    FindingCategory,
    FindingSeverity,
    StrictModel,
)


class EvidenceDraft(StrictModel):
    source: Literal["diff", "repository"]
    file_path: Annotated[str, Field(min_length=1)]
    line_start: Annotated[int, Field(gt=0)] | None = None
    line_end: Annotated[int, Field(gt=0)] | None = None
    excerpt: Annotated[str, Field(max_length=4_000)] | None = None


class TransientProviderError(Exception):
    """A provider/network failure for which a bounded retry is appropriate."""


class MalformedProviderOutputError(Exception):
    """The provider returned data that violates the review response contract."""


FinishReason = Literal[
    "completed",
    "length_limit",
    "content_filtered",
    "provider_error",
    "other",
]


class FindingDraft(StrictModel):
    category: FindingCategory
    severity: FindingSeverity
    confidence: Annotated[float, Field(ge=0, le=1)]
    file_path: Annotated[str, Field(min_length=1)]
    line_start: Annotated[int, Field(gt=0)] | None = None
    line_end: Annotated[int, Field(gt=0)] | None = None
    side: Literal["LEFT", "RIGHT"] | None = None
    title: Annotated[str, Field(min_length=1)]
    defect: Annotated[str, Field(min_length=1)]
    trigger: Annotated[str, Field(min_length=1)]
    impact: Annotated[str, Field(min_length=1)]
    recommendation: Annotated[str, Field(min_length=1)]
    evidence: list[EvidenceDraft] = Field(default_factory=list)


class ModelUsage(StrictModel):
    prompt_tokens: Annotated[int, Field(ge=0)] = 0
    completion_tokens: Annotated[int, Field(ge=0)] = 0
    total_tokens: Annotated[int, Field(ge=0)] = 0
    estimated_cost_usd: Annotated[float, Field(ge=0)] | None = None


class OutputConstraints(StrictModel):
    schema_version: Literal["1"]
    max_output_tokens: Annotated[int, Field(gt=0, le=8_000)]
    allow_empty_findings: bool = True


class ReviewModelRequest(StrictModel):
    prompt: Annotated[str, Field(min_length=1, max_length=20_000)]
    diff_context: Annotated[str, Field(min_length=1, max_length=1_000_000)]
    output_constraints: OutputConstraints
    model_name: Annotated[str, Field(min_length=1, max_length=200)]
    timeout: Annotated[int, Field(gt=0, le=120)]


class ReviewResponseModel(StrictModel):
    draft_findings: list[FindingDraft] = Field(default_factory=list)
    usage: ModelUsage
    model_name: Annotated[str, Field(min_length=1, max_length=200)]
    finish_reason: FinishReason


class LLMProvider(ABC):
    @abstractmethod
    async def generate_review(self, request: ReviewModelRequest) -> ReviewResponseModel:

        raise NotImplementedError
