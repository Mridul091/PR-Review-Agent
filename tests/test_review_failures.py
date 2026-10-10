"""Review retries and safe, typed failures through the service and API."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.review import build_review_result, router
from src.github.diff_parser import DiffHunk, DiffLine, FileDiff
from src.github.errors import GitHubClientError, InputLimitExceededError
from src.llm.base import (
    EvidenceDraft,
    FindingDraft,
    MalformedProviderOutputError,
    ModelUsage,
    ReviewResponseModel,
    TransientProviderError,
)
from src.llm.groq_provider import GroqProvider
from src.middleware.auth import AuthenticationMiddleware
from src.review.schemas import ReviewRequest, ReviewStatus
from src.review.service import ReviewService


class _Auth:
    async def get_installation_token(self, installation_id):
        return "token"


class _GitHub:
    async def get_pull_request(self, repo, number, token):
        return SimpleNamespace(
            target_repo_full_name="owner/repo",
            number=1,
            base_sha="a" * 40,
            head_sha="b" * 40,
        )

    async def get_pr_diff(self, repo, number, token):
        return [
            FileDiff(
                file_path="src/app.py",
                language="Python",
                hunks=[
                    DiffHunk(
                        0,
                        0,
                        1,
                        1,
                        lines=[DiffLine("addition", "x = 1", new_line_no=1)],
                    )
                ],
            )
        ]


class _Provider:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    async def generate_review(self, request):
        self.calls += 1
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _request():
    return ReviewRequest(
        repository="owner/repo",
        installation_id=12,
        pull_request_number=1,
        base_sha="a" * 40,
        head_sha="b" * 40,
    )


def _body():
    return _request().model_dump()


def _response():
    return ReviewResponseModel(
        draft_findings=[], usage=ModelUsage(), model_name="fake", finish_reason="completed"
    )


def _service(provider, auth=None, github=None, **kwargs):
    return ReviewService(
        auth or _Auth(), github or _GitHub(), provider, model_name="fake", **kwargs
    )


@pytest.mark.asyncio
async def test_transient_failure_retries_once_then_succeeds():
    provider = _Provider(TransientProviderError("secret"), _response())
    result = build_review_result(await _service(provider).review(_request()))
    assert result.status == ReviewStatus.COMPLETED
    assert provider.calls == result.usage.model_calls == 2
    assert result.failures == []


@pytest.mark.asyncio
async def test_transient_retries_are_bounded_and_exhaustion_is_typed():
    provider = _Provider(*[TransientProviderError("secret") for _ in range(3)])
    result = build_review_result(await _service(provider, max_model_attempts=3).review(_request()))
    assert result.status == ReviewStatus.FAILED
    assert result.failures[0].code == "provider_error"
    assert result.failures[0].retryable
    assert "secret" not in result.failures[0].message
    assert result.agents_failed == ["bug_detector"]
    assert result.usage.model_calls == provider.calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        MalformedProviderOutputError("raw provider text"),
        {"draft_findings": [{"title": "incomplete"}]},
    ],
)
async def test_malformed_output_is_not_retried(bad):
    provider = _Provider(bad, _response())
    result = build_review_result(await _service(provider).review(_request()))
    assert result.status == ReviewStatus.FAILED
    assert result.failures[0].code == "malformed_model_output"
    assert not result.failures[0].retryable
    assert result.usage.model_calls == provider.calls == 1


@pytest.mark.asyncio
async def test_invalid_finding_contract_is_a_typed_failure():
    provider = _Provider(
        ReviewResponseModel(
            draft_findings=[
                FindingDraft(
                    category="bug",
                    severity="medium",
                    confidence=0.8,
                    file_path="src/app.py",
                    line_start=1,
                    side="RIGHT",
                    title="x" * 201,
                    defect="Defect",
                    trigger="Trigger",
                    impact="Impact",
                    recommendation="Fix it",
                    evidence=[EvidenceDraft(source="diff", file_path="src/app.py", line_start=1)],
                )
            ],
            usage=ModelUsage(),
            model_name="fake",
            finish_reason="completed",
        )
    )
    result = build_review_result(await _service(provider).review(_request()))
    assert result.status == ReviewStatus.FAILED
    assert result.failures[0].code == "malformed_model_output"
    assert result.usage.model_calls == 1


@pytest.mark.asyncio
async def test_permanent_provider_error_is_not_retried():
    provider = _Provider(RuntimeError("private payload"), _response())
    result = build_review_result(await _service(provider).review(_request()))
    assert result.failures[0].code == "provider_error"
    assert not result.failures[0].retryable
    assert "private payload" not in result.failures[0].message
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_elapsed_timeout_is_typed_and_not_retried():
    class SlowProvider:
        calls = 0

        async def generate_review(self, request):
            self.calls += 1
            await asyncio.sleep(2)
            return _response()

    provider = SlowProvider()
    result = build_review_result(await _service(provider, timeout=1).review(_request()))
    assert result.status == ReviewStatus.FAILED
    assert result.failures[0].code == "provider_timeout"
    assert result.usage.model_calls == provider.calls == 1


@pytest.mark.asyncio
async def test_ingestion_errors_are_typed_and_make_no_model_calls():
    class BadGitHub(_GitHub):
        def __init__(self, error):
            self.error = error

        async def get_pr_diff(self, repo, number, token):
            raise self.error

    provider = _Provider(_response())
    for error, code, status in (
        (
            InputLimitExceededError("diff_too_large", "Diff too large."),
            "diff_too_large",
            ReviewStatus.REJECTED,
        ),
        (
            GitHubClientError("github_timeout", "GitHub timed out.", retryable=True),
            "github_timeout",
            ReviewStatus.FAILED,
        ),
        (ValueError("raw diff content"), "invalid_diff", ReviewStatus.REJECTED),
    ):
        run = await _service(provider, github=BadGitHub(error)).review(_request())
        result = build_review_result(run)
        assert result.status == status
        assert result.failures[0].code == code
        assert result.usage.model_calls == 0
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_auth_errors_are_sanitized():
    class BadAuth:
        async def get_installation_token(self, installation_id):
            raise FileNotFoundError("private key path /secret/key.pem")

    provider = _Provider(_response())
    result = build_review_result(await _service(provider, auth=BadAuth()).review(_request()))
    assert result.status == ReviewStatus.FAILED
    assert result.failures[0].code == "github_auth_failed"
    assert "/secret/" not in result.failures[0].message
    assert provider.calls == 0


def test_api_exposes_malformed_output_as_typed_failed_review():
    app = FastAPI()
    app.add_middleware(AuthenticationMiddleware)
    app.include_router(router, prefix="/api/v1")
    provider = _Provider(MalformedProviderOutputError("raw provider payload"))
    app.state.review_service = _service(provider)
    app.state.review_api_token = "test-token"
    app.state.review_allowed_repositories = frozenset({(12, "owner/repo")})
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
        response = client.post("/api/v1/reviews", json=_body())
        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert response.json()["failures"][0]["code"] == "malformed_model_output"
        assert "raw provider payload" not in response.text
        assert response.json()["usage"]["model_calls"] == 1
    assert provider.calls == 1


def test_api_returns_and_stores_typed_ingestion_failure():
    class BadGitHub(_GitHub):
        async def get_pr_diff(self, repo, number, token):
            raise InputLimitExceededError("diff_too_large", "Diff too large.")

    app = FastAPI()
    app.add_middleware(AuthenticationMiddleware)
    app.include_router(router, prefix="/api/v1")
    app.state.review_service = _service(_Provider(_response()), github=BadGitHub())
    app.state.review_api_token = "test-token"
    app.state.review_allowed_repositories = frozenset({(12, "owner/repo")})
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
        response = client.post("/api/v1/reviews", json=_body())
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert response.json()["failures"][0]["code"] == "diff_too_large"
        fetched = client.get(f"/api/v1/reviews/{response.json()['review_id']}")
        assert fetched.json() == response.json()


@pytest.mark.asyncio
async def test_groq_invalid_json_is_classified_as_malformed():
    class Completions:
        async def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{"))])

    provider = GroqProvider(api_key="unused")
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    from src.llm.base import OutputConstraints, ReviewModelRequest

    request = ReviewModelRequest(
        prompt="review",
        diff_context="diff",
        model_name="fake",
        timeout=1,
        output_constraints=OutputConstraints(schema_version="1", max_output_tokens=1),
    )
    with pytest.raises(MalformedProviderOutputError):
        await provider.generate_review(request)
