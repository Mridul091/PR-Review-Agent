"""Stable, credential-free contracts shared by the review pipeline and API."""

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PositiveInt = Annotated[int, Field(gt=0)]
CommitSha = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{7,64}$")]
RepositoryName = Annotated[str, Field(pattern=r"^[^/\s]+/[^/\s]+$")]


class StrictModel(BaseModel):
    """Reject unknown data so credentials cannot leak into graph state by accident."""

    model_config = ConfigDict(extra="forbid")


class ReviewStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    REJECTED = "rejected"
    QUEUED = "queued"
    RUNNING = "running"


class FindingCategory(str, Enum):
    SECURITY = "security"
    BUG = "bug"
    QUALITY = "quality"
    PERFORMANCE = "performance"
    TESTING = "testing"


class FindingSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Evidence(StrictModel):
    """A source location that supports a finding."""

    source: Literal["diff", "repository"]
    file_path: Annotated[str, Field(min_length=1)]
    commit_sha: CommitSha
    line_start: PositiveInt | None = None
    line_end: PositiveInt | None = None
    excerpt: Annotated[str, Field(max_length=4_000)] | None = None

    @model_validator(mode="after")
    def validate_line_range(self) -> "Evidence":
        line_start = self.line_start
        line_end = self.line_end

        if line_end is not None and line_start is None:
            raise ValueError("line_end requires line_start")
        if line_end is not None and line_start is not None and line_end < line_start:
            raise ValueError("line_end must be greater than or equal to line_start")
        return self


class ReviewRequest(StrictModel):
    """Identity and immutable commit bounds for one requested review."""

    repository: RepositoryName
    installation_id: PositiveInt
    pull_request_number: PositiveInt
    base_sha: CommitSha
    head_sha: CommitSha
    requested_agents: list[Annotated[str, Field(min_length=1)]] | None = None

    @model_validator(mode="after")
    def commits_must_differ(self) -> "ReviewRequest":
        if self.base_sha.lower() == self.head_sha.lower():
            raise ValueError("base_sha and head_sha must differ")
        return self


class Finding(StrictModel):
    """A validated, attributable issue produced by a reviewer."""

    id: Annotated[str, Field(min_length=1, max_length=128)]
    category: FindingCategory
    severity: FindingSeverity
    confidence: Annotated[float, Field(ge=0, le=1)]
    file_path: Annotated[str, Field(min_length=1)]
    line_start: PositiveInt | None = None
    line_end: PositiveInt | None = None
    side: Literal["LEFT", "RIGHT"] | None = None
    title: Annotated[str, Field(min_length=1, max_length=200)]
    defect: Annotated[str, Field(min_length=1, max_length=8_000)]
    trigger: Annotated[str, Field(min_length=1, max_length=8_000)]
    impact: Annotated[str, Field(min_length=1, max_length=8_000)]
    recommendation: Annotated[str, Field(min_length=1, max_length=8_000)]
    agent: Annotated[str, Field(min_length=1)]
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_location(self) -> "Finding":
        line_start = self.line_start
        line_end = self.line_end

        if line_end is not None and line_start is None:
            raise ValueError("line_end requires line_start")
        if line_end is not None and line_start is not None and line_end < line_start:
            raise ValueError("line_end must be greater than or equal to line_start")
        if self.side is not None and line_start is None:
            raise ValueError("side requires a line_start")
        return self


class ReviewFailure(StrictModel):
    """A typed, user-safe failure from one review stage."""

    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    stage: Literal["input", "github", "review", "aggregation", "system"]
    message: Annotated[str, Field(min_length=1, max_length=1_000)]
    retryable: bool = False
    agent: str | None = None


class ReviewUsage(StrictModel):
    prompt_tokens: Annotated[int, Field(ge=0)] = 0
    completion_tokens: Annotated[int, Field(ge=0)] = 0
    model_calls: Annotated[int, Field(ge=0)] = 0
    estimated_cost_usd: Annotated[float, Field(ge=0)] = 0


class ReviewResult(StrictModel):
    """Complete response contract for successful and failed review attempts."""

    review_id: Annotated[str, Field(min_length=1)]
    request: ReviewRequest
    status: ReviewStatus
    findings: list[Finding] = Field(default_factory=list)
    agents_attempted: list[str] = Field(default_factory=list)
    agents_failed: list[str] = Field(default_factory=list)
    failures: list[ReviewFailure] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime
    duration_ms: Annotated[int, Field(ge=0)]
    usage: ReviewUsage = Field(default_factory=ReviewUsage)

    @model_validator(mode="after")
    def validate_outcome(self) -> "ReviewResult":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        if self.status in {ReviewStatus.FAILED, ReviewStatus.REJECTED} and not self.failures:
            raise ValueError("failed and rejected results require at least one failure")
        unknown_failures = set(self.agents_failed) - set(self.agents_attempted)
        if unknown_failures:
            raise ValueError("agents_failed must be a subset of agents_attempted")
        return self
