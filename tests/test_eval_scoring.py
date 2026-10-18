"""Matching rules and metrics for the evaluation harness."""

import pytest

from evals.dataset import EvalCase, ExpectedFinding
from evals.scoring import aggregate, score_case
from src.review.schemas import Finding, FindingCategory, FindingSeverity


def _case(*expected: ExpectedFinding, case_id: str = "case") -> EvalCase:
    return EvalCase(id=case_id, description="d", split="dev", expected=list(expected), diff="x")


def _expected(line: int = 10, path: str = "a.py", line_end: int | None = None) -> ExpectedFinding:
    return ExpectedFinding(
        file_path=path, line_start=line, line_end=line_end, defect_type="bug", summary="s"
    )


def _finding(
    line: int | None = 10,
    path: str = "a.py",
    side: str | None = "RIGHT",
    category: FindingCategory = FindingCategory.BUG,
) -> Finding:
    return Finding(
        id="f",
        category=category,
        severity=FindingSeverity.HIGH,
        confidence=0.9,
        file_path=path,
        line_start=line,
        side=side if line is not None else None,  # type: ignore[arg-type]
        title="t",
        defect="d",
        trigger="t",
        impact="i",
        recommendation="r",
        agent="bug_detector",
    )


def test_exact_location_is_a_true_positive():
    score = score_case(_case(_expected(10)), [_finding(10)])
    assert (score.tp, score.fp, score.fn) == (1, 0, 0)
    assert score.matches[0].location_exact
    assert score.matches[0].category_match


@pytest.mark.parametrize("line, matched", [(13, True), (7, True), (14, False), (6, False)])
def test_line_tolerance(line, matched):
    score = score_case(_case(_expected(10)), [_finding(line)], tolerance=3)
    assert score.tp == int(matched)
    assert score.fp == int(not matched)
    if matched:
        assert not score.matches[0].location_exact


def test_overlapping_ranges_are_exact():
    score = score_case(_case(_expected(10, line_end=14)), [_finding(12)])
    assert score.matches[0].location_exact


def test_wrong_file_does_not_match():
    score = score_case(_case(_expected(10)), [_finding(10, path="b.py")])
    assert (score.tp, score.fp, score.fn) == (0, 1, 1)


def test_wrong_side_does_not_match():
    score = score_case(_case(_expected(10)), [_finding(10, side="LEFT")])
    assert score.tp == 0


def test_lineless_finding_matches_on_file_but_not_exactly():
    score = score_case(_case(_expected(10)), [_finding(None)])
    assert score.tp == 1
    assert not score.matches[0].location_exact


def test_matching_is_one_to_one_and_prefers_closest():
    score = score_case(_case(_expected(10)), [_finding(12), _finding(10)])
    assert score.tp == 1 and score.fp == 1
    assert score.matches[0].finding_index == 1
    assert score.false_positive_indices == [0]


def test_category_mismatch_still_matches_but_is_recorded():
    score = score_case(_case(_expected(10)), [_finding(10, category=FindingCategory.QUALITY)])
    assert score.tp == 1
    assert not score.matches[0].category_match


def test_failed_review_misses_every_expected_finding():
    score = score_case(_case(_expected(10), _expected(20)), [], failure_code="provider_error")
    assert (score.tp, score.fp, score.fn) == (0, 0, 2)


def test_aggregate_metrics():
    bug = score_case(_case(_expected(10), _expected(30)), [_finding(10), _finding(50)])
    clean_noisy = score_case(_case(case_id="clean1"), [_finding(5)])
    clean_quiet = score_case(_case(case_id="clean2"), [])
    metrics = aggregate([bug, clean_noisy, clean_quiet])
    assert (metrics.tp, metrics.fp, metrics.fn) == (1, 2, 1)
    assert metrics.precision == pytest.approx(1 / 3)
    assert metrics.recall == pytest.approx(1 / 2)
    assert metrics.f1 == pytest.approx(0.4)
    assert metrics.clean_fp_rate == pytest.approx(0.5)
    assert metrics.location_exact_rate == 1.0


def test_metrics_are_none_without_data():
    metrics = aggregate([score_case(_case(case_id="clean"), [])])
    assert metrics.precision is None
    assert metrics.recall is None
    assert metrics.f1 is None
    assert metrics.clean_fp_rate == 0.0
