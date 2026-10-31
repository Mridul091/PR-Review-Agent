"""Match reviewer findings to expected findings and compute metrics.

Matching rule: a finding matches an expected defect when both refer to the same
file and diff side and their line ranges are within ``tolerance`` lines of each
other. A finding without line numbers matches on file alone but does not count
as an exact location. Each expected defect matches at most one finding and
vice versa; closer pairs are matched first.
"""

from dataclasses import dataclass, field

from evals.dataset import EvalCase, ExpectedFinding
from src.review.schemas import Finding

DEFAULT_TOLERANCE = 3


@dataclass(frozen=True)
class Match:
    expected_index: int
    finding_index: int
    location_exact: bool
    category_match: bool


@dataclass
class CaseScore:
    case_id: str
    is_clean: bool
    failure_code: str | None = None
    matches: list[Match] = field(default_factory=list)
    false_positive_indices: list[int] = field(default_factory=list)
    missed_indices: list[int] = field(default_factory=list)

    @property
    def tp(self) -> int:
        return len(self.matches)

    @property
    def fp(self) -> int:
        return len(self.false_positive_indices)

    @property
    def fn(self) -> int:
        return len(self.missed_indices)


@dataclass(frozen=True)
class Metrics:
    cases: int
    bug_cases: int
    clean_cases: int
    failed_cases: int
    tp: int
    fp: int
    fn: int
    precision: float | None
    recall: float | None
    f1: float | None
    clean_fp_rate: float | None
    location_exact_rate: float | None
    category_match_rate: float | None


def _line_distance(finding: Finding, expected: ExpectedFinding) -> int | None:
    """Gap in lines between the two ranges (0 when they overlap); None if line-less."""
    if finding.line_start is None:
        return None
    f_start, f_end = finding.line_start, finding.line_end or finding.line_start
    e_start, e_end = expected.line_start, expected.line_end or expected.line_start
    return max(0, max(f_start, e_start) - min(f_end, e_end))


def score_case(
    case: EvalCase,
    findings: list[Finding],
    *,
    failure_code: str | None = None,
    tolerance: int = DEFAULT_TOLERANCE,
) -> CaseScore:
    """Score one review. A failed review misses every expected finding."""
    score = CaseScore(case_id=case.id, is_clean=case.is_clean, failure_code=failure_code)
    if failure_code is not None:
        score.missed_indices = list(range(len(case.expected)))
        return score

    candidates: list[tuple[int, int, int]] = []  # (rank, expected_index, finding_index)
    for e_index, expected in enumerate(case.expected):
        for f_index, finding in enumerate(findings):
            if finding.file_path != expected.file_path:
                continue
            if finding.side is not None and finding.side != expected.side:
                continue
            distance = _line_distance(finding, expected)
            if distance is None:
                candidates.append((tolerance + 1, e_index, f_index))
            elif distance <= tolerance:
                candidates.append((distance, e_index, f_index))

    used_expected: set[int] = set()
    used_findings: set[int] = set()
    for rank, e_index, f_index in sorted(candidates):
        if e_index in used_expected or f_index in used_findings:
            continue
        used_expected.add(e_index)
        used_findings.add(f_index)
        score.matches.append(
            Match(
                expected_index=e_index,
                finding_index=f_index,
                location_exact=rank == 0,
                category_match=findings[f_index].category == case.expected[e_index].category,
            )
        )

    score.false_positive_indices = [i for i in range(len(findings)) if i not in used_findings]
    score.missed_indices = [i for i in range(len(case.expected)) if i not in used_expected]
    return score


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def aggregate(scores: list[CaseScore]) -> Metrics:
    tp = sum(score.tp for score in scores)
    fp = sum(score.fp for score in scores)
    fn = sum(score.fn for score in scores)
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall > 0
        else None
    )
    clean = [score for score in scores if score.is_clean]
    matches = [match for score in scores for match in score.matches]
    return Metrics(
        cases=len(scores),
        bug_cases=len(scores) - len(clean),
        clean_cases=len(clean),
        failed_cases=sum(1 for score in scores if score.failure_code is not None),
        tp=tp,
        fp=fp,
        fn=fn,
        precision=precision,
        recall=recall,
        f1=f1,
        clean_fp_rate=_ratio(sum(1 for score in clean if score.fp), len(clean)),
        location_exact_rate=_ratio(sum(m.location_exact for m in matches), len(matches)),
        category_match_rate=_ratio(sum(m.category_match for m in matches), len(matches)),
    )
