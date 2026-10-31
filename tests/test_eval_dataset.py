"""Dataset integrity and an end-to-end harness run with the fake provider."""

from datetime import datetime, timezone

import pytest

from evals.dataset import load_cases
from evals.make_case import make_patch
from evals.report import RunInfo, render_markdown
from evals.run import prompt_version
from evals.runner import run_case, run_dataset
from src.github.diff_parser import parse_diff
from src.llm.base import EvidenceDraft, FindingDraft, ModelUsage, ReviewResponseModel
from src.llm.fake import FakeLLMProvider

CASES = load_cases()


def test_dataset_has_bug_and_clean_cases_with_unique_ids():
    assert len({case.id for case in CASES}) == len(CASES)
    assert any(case.is_clean for case in CASES)
    assert any(not case.is_clean for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_expected_locations_exist_in_the_diff(case):
    files = {file_diff.file_path: file_diff for file_diff in parse_diff(case.diff)}
    for expected in case.expected:
        assert expected.file_path in files, f"{expected.file_path} not in diff"
        side_lines = {
            line.new_line_no if expected.side == "RIGHT" else line.old_line_no
            for hunk in files[expected.file_path].hunks
            for line in hunk.lines
        }
        for line_no in (expected.line_start, expected.line_end):
            if line_no is not None:
                assert line_no in side_lines, f"line {line_no} not on {expected.side} side"


def test_make_patch_round_trips_through_the_parser():
    patch = make_patch("pkg/mod.py", "a = 1\nb = 2\n", "a = 1\nb = 3\nc = 4\n")
    [file_diff] = parse_diff(patch)
    assert file_diff.file_path == "pkg/mod.py"
    assert [(line.new_line_no, line.content) for line in file_diff.added_lines] == [
        (2, "b = 3"),
        (3, "c = 4"),
    ]


def test_make_patch_supports_new_files():
    [file_diff] = parse_diff(make_patch("new.py", "", "x = 1\n"))
    assert file_diff.is_new_file


@pytest.mark.asyncio
async def test_fake_provider_run_scores_clean_cases_as_correct():
    results = await run_dataset(CASES, FakeLLMProvider(), model_name="fake")
    metrics = results[0].metrics
    assert metrics.failed_cases == 0
    assert metrics.fp == 0
    assert metrics.clean_fp_rate == 0.0
    assert metrics.recall == 0.0  # the fake provider never reports findings
    assert "Error analysis" in render_markdown(_info(), results)


@pytest.mark.asyncio
async def test_a_correct_finding_is_scored_as_true_positive():
    case = next(case for case in CASES if case.id == "off_by_one_pagination")
    expected = case.expected[0]
    response = ReviewResponseModel(
        draft_findings=[
            FindingDraft(
                category=expected.category,
                severity="high",  # type: ignore[arg-type]
                confidence=0.9,
                file_path=expected.file_path,
                line_start=expected.line_start,
                side="RIGHT",
                title="Off-by-one in page offset",
                defect="d",
                trigger="t",
                impact="i",
                recommendation="r",
                evidence=[
                    EvidenceDraft(
                        source="diff", file_path=expected.file_path, line_start=expected.line_start
                    )
                ],
            )
        ],
        usage=ModelUsage(),
        model_name="fake",
        finish_reason="completed",
    )
    run = await run_case(case, FakeLLMProvider([response]), model_name="fake")
    assert (run.score.tp, run.score.fp, run.score.fn) == (1, 0, 0)


def _info() -> RunInfo:
    return RunInfo(
        timestamp=datetime.now(timezone.utc),
        dataset="evals/datasets/v1",
        split="dev",
        provider="fake",
        model="fake",
        prompt_version=prompt_version(),
        repeats=1,
        tolerance=3,
        git_sha="test",
        git_dirty=False,
    )
