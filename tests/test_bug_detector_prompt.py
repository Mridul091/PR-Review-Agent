"""The prompt must name every enum value the response schema accepts."""

import json
import re

import pytest

from src.agents.bug_detector import BUG_DETECTOR_PROMPT
from src.llm.base import FindingDraft
from src.review.schemas import FindingCategory, FindingSeverity


@pytest.mark.parametrize("value", [*FindingCategory, *FindingSeverity])
def test_prompt_lists_every_enum_value(value: FindingCategory | FindingSeverity) -> None:
    assert value.value in BUG_DETECTOR_PROMPT


def test_prompt_example_matches_the_response_schema() -> None:
    start = BUG_DETECTOR_PROMPT.index('{"category"')
    end = BUG_DETECTOR_PROMPT.index("Return an empty draft_findings")
    example = re.sub(r"\s+", " ", BUG_DETECTOR_PROMPT[start:end]).strip()
    FindingDraft.model_validate(json.loads(example))
