"""Run evaluation cases through the real ReviewService with a local GitHub stand-in."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from evals.dataset import EvalCase
from evals.scoring import DEFAULT_TOLERANCE, CaseScore, Metrics, aggregate, score_case
from src.api.review import build_review_result
from src.github.auth import GitHubAppAuth
from src.github.client import GitHubClient, PRAuthor, PRMetadata
from src.github.diff_parser import FileDiff, parse_diff
from src.llm.base import LLMProvider
from src.review.schemas import Finding, ReviewRequest
from src.review.service import ReviewService

_REPOSITORY = "evals/dataset"
_BASE_SHA = "a" * 40
_HEAD_SHA = "b" * 40


class _EvalAuth:
    async def get_installation_token(self, installation_id: int) -> str:
        return "eval-token"


class _EvalGitHub:
    """Serves one case's diff in place of the GitHub API."""

    def __init__(self, case: EvalCase) -> None:
        self._case = case

    async def get_pull_request(self, repo: str, pr_number: int, token: str) -> PRMetadata:
        return PRMetadata(
            number=1,
            title=self._case.id,
            body=self._case.description,
            state="open",
            author=PRAuthor(login="eval", avatar_url="", html_url=""),
            base_branch="main",
            base_sha=_BASE_SHA,
            head_branch=self._case.id,
            head_sha=_HEAD_SHA,
            target_repo_full_name=_REPOSITORY,
            head_repo_full_name=_REPOSITORY,
            html_url="",
            draft=False,
            changed_files=0,
            additions=0,
            deletions=0,
        )

    async def get_pr_diff(self, repo: str, pr_number: int, token: str) -> list[FileDiff]:
        return parse_diff(self._case.diff)


@dataclass
class CaseRun:
    case: EvalCase
    repeat: int
    status: str
    findings: list[Finding]
    failure_code: str | None
    duration_ms: int
    prompt_tokens: int
    completion_tokens: int
    model_calls: int
    score: CaseScore


@dataclass
class RepeatResult:
    repeat: int
    case_runs: list[CaseRun]
    metrics: Metrics


async def run_case(
    case: EvalCase,
    provider: LLMProvider,
    *,
    model_name: str,
    repeat: int = 1,
    tolerance: int = DEFAULT_TOLERANCE,
    max_context_chars: int = 20_000,
    max_model_attempts: int = 2,
    max_output_tokens: int = 8_000,
    timeout: int = 30,
) -> CaseRun:
    service = ReviewService(
        cast(GitHubAppAuth, _EvalAuth()),
        cast(GitHubClient, _EvalGitHub(case)),
        provider,
        model_name=model_name,
        max_context_chars=max_context_chars,
        max_model_attempts=max_model_attempts,
        max_output_tokens=max_output_tokens,
        timeout=timeout,
    )
    request = ReviewRequest(
        repository=_REPOSITORY,
        installation_id=1,
        pull_request_number=1,
        base_sha=_BASE_SHA,
        head_sha=_HEAD_SHA,
    )
    started = time.perf_counter()
    result = build_review_result(await service.review(request))
    duration_ms = int((time.perf_counter() - started) * 1000)
    failure_code = result.failures[0].code if result.failures else None
    return CaseRun(
        case=case,
        repeat=repeat,
        status=result.status.value,
        findings=result.findings,
        failure_code=failure_code,
        duration_ms=duration_ms,
        prompt_tokens=result.usage.prompt_tokens,
        completion_tokens=result.usage.completion_tokens,
        model_calls=result.usage.model_calls,
        score=score_case(case, result.findings, failure_code=failure_code, tolerance=tolerance),
    )


async def run_dataset(
    cases: list[EvalCase],
    provider: LLMProvider,
    *,
    model_name: str,
    repeats: int = 1,
    tolerance: int = DEFAULT_TOLERANCE,
    max_context_chars: int = 20_000,
    max_model_attempts: int = 2,
    max_output_tokens: int = 8_000,
    timeout: int = 30,
    on_case: Callable[["CaseRun"], None] | None = None,
) -> list[RepeatResult]:
    """Run every case ``repeats`` times, sequentially to respect provider rate limits."""
    results: list[RepeatResult] = []
    for repeat in range(1, repeats + 1):
        case_runs: list[CaseRun] = []
        for case in cases:
            case_run = await run_case(
                case,
                provider,
                model_name=model_name,
                repeat=repeat,
                tolerance=tolerance,
                max_context_chars=max_context_chars,
                max_model_attempts=max_model_attempts,
                max_output_tokens=max_output_tokens,
                timeout=timeout,
            )
            if on_case is not None:
                on_case(case_run)
            case_runs.append(case_run)
        results.append(
            RepeatResult(
                repeat=repeat,
                case_runs=case_runs,
                metrics=aggregate([run.score for run in case_runs]),
            )
        )
    return results
