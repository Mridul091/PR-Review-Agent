"""Context budgeting must reject oversized reviews before calling the provider."""

from types import SimpleNamespace

import pytest

from src.agents.reviewer import format_review_context
from src.api.review import build_review_result
from src.github.diff_parser import DiffHunk, DiffLine, FileDiff
from src.llm.fake import FakeLLMProvider
from src.review.schemas import ReviewRequest, ReviewStatus
from src.review.service import ReviewService


class _Auth:
    async def get_installation_token(self, installation_id: int) -> str:
        return "test-token"


class _GitHub:
    def __init__(self, files):
        self.files = files

    async def get_pull_request(self, repo, pr_number, token):
        return SimpleNamespace(
            target_repo_full_name="owner/repo",
            number=1,
            base_sha="a" * 40,
            head_sha="b" * 40,
        )

    async def get_pr_diff(self, repo, pr_number, token):
        return self.files


def _request():
    return ReviewRequest(
        repository="owner/repo",
        installation_id=12,
        pull_request_number=1,
        base_sha="a" * 40,
        head_sha="b" * 40,
    )


def _files():
    return [
        FileDiff(
            file_path="src/app.py",
            language="Python",
            hunks=[
                DiffHunk(
                    old_start=0,
                    old_lines=0,
                    new_start=1,
                    new_lines=1,
                    lines=[DiffLine("addition", "x = 1", new_line_no=1)],
                )
            ],
        )
    ]


@pytest.mark.asyncio
async def test_oversized_context_returns_rejected_result_without_model_call():
    files = _files()
    limit = len(format_review_context(files)) - 1
    provider = FakeLLMProvider()
    service = ReviewService(
        _Auth(), _GitHub(files), provider, model_name="fake", max_context_chars=limit
    )

    result = build_review_result(await service.review(_request()))

    assert result.status == ReviewStatus.REJECTED
    assert result.failures[0].code == "review_context_too_large"
    assert result.failures[0].stage == "input"
    assert result.findings == []
    assert result.usage.model_calls == 0
    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_context_at_limit_is_reviewed_and_clean_result_is_valid():
    files = _files()
    provider = FakeLLMProvider()
    service = ReviewService(
        _Auth(),
        _GitHub(files),
        provider,
        model_name="fake",
        max_context_chars=len(format_review_context(files)),
    )

    result = build_review_result(await service.review(_request()))

    assert result.status == ReviewStatus.COMPLETED
    assert result.findings == []
    assert provider.call_count == 1
    assert provider.requests[0].diff_context == format_review_context(files)


def test_context_limit_cannot_exceed_model_schema_limit():
    with pytest.raises(ValueError, match="max_context_chars"):
        ReviewService(
            _Auth(),
            _GitHub([]),
            FakeLLMProvider(),
            model_name="fake",
            max_context_chars=1_000_001,
        )
