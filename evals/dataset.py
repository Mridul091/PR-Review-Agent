"""Evaluation case format and loader.

Each case is a directory containing:

    case.toml    metadata and expected findings
    diff.patch   the pull-request diff fed to the reviewer
"""

import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from src.review.schemas import FindingCategory

Split = Literal["dev", "heldout"]
DEFAULT_DATASET = Path(__file__).parent / "datasets" / "v1"


class ExpectedFinding(BaseModel):
    """A known defect the reviewer should report."""

    model_config = ConfigDict(extra="forbid")

    file_path: Annotated[str, Field(min_length=1)]
    line_start: PositiveInt
    line_end: PositiveInt | None = None
    side: Literal["LEFT", "RIGHT"] = "RIGHT"
    category: FindingCategory = FindingCategory.BUG
    defect_type: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    summary: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def _check_range(self) -> "ExpectedFinding":
        if self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("line_end must be >= line_start")
        return self


class EvalCase(BaseModel):
    """One labelled pull request."""

    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    description: Annotated[str, Field(min_length=1)]
    split: Split
    tags: list[str] = Field(default_factory=list)
    expected: list[ExpectedFinding] = Field(default_factory=list)
    diff: Annotated[str, Field(min_length=1)]

    @property
    def is_clean(self) -> bool:
        """A clean case has no expected findings; any finding is a false positive."""
        return not self.expected


def load_case(case_dir: Path) -> EvalCase:
    with (case_dir / "case.toml").open("rb") as file:
        data = tomllib.load(file)
    case = EvalCase.model_validate({**data, "diff": (case_dir / "diff.patch").read_text()})
    if case.id != case_dir.name:
        raise ValueError(f"Case id {case.id!r} must match its directory name {case_dir.name!r}")
    return case


def load_cases(dataset_dir: Path = DEFAULT_DATASET, split: Split | None = None) -> list[EvalCase]:
    """Load every case in a dataset, optionally filtered to one split."""
    cases_dir = dataset_dir / "cases"
    cases = [load_case(path) for path in sorted(cases_dir.iterdir()) if path.is_dir()]
    if split is not None:
        cases = [case for case in cases if case.split == split]
    return cases
