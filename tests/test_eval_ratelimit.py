"""The eval wrapper waits out rate limits instead of failing the review."""

import pytest

from evals import ratelimit
from evals.ratelimit import RateLimitRetryProvider
from src.llm.base import (
    LLMProvider,
    ModelUsage,
    OutputConstraints,
    RateLimitedError,
    ReviewModelRequest,
    ReviewResponseModel,
)


class _Flaky(LLMProvider):
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def generate_review(self, request: ReviewModelRequest) -> ReviewResponseModel:
        self.calls += 1
        if self.calls <= self.failures:
            raise RateLimitedError("limit", retry_after=7)
        return ReviewResponseModel(
            usage=ModelUsage(
                prompt_tokens=0, completion_tokens=0, total_tokens=0, estimated_cost_usd=0
            ),
            model_name="m",
            finish_reason="completed",
        )


def _request() -> ReviewModelRequest:
    return ReviewModelRequest(
        prompt="p",
        diff_context="d",
        output_constraints=OutputConstraints(
            schema_version="1", max_output_tokens=100, allow_empty_findings=True
        ),
        model_name="m",
        timeout=30,
    )


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(ratelimit.asyncio, "sleep", fake_sleep)
    return recorded


async def test_waits_for_retry_after_then_succeeds(sleeps: list[float]) -> None:
    provider = RateLimitRetryProvider(_Flaky(failures=1))
    response = await provider.generate_review(_request())
    assert response.finish_reason == "completed"
    assert sleeps == [7.5]
    assert provider.waits == 1


async def test_gives_up_after_max_waits(sleeps: list[float]) -> None:
    provider = RateLimitRetryProvider(_Flaky(failures=10), max_waits=2)
    with pytest.raises(RateLimitedError):
        await provider.generate_review(_request())
    assert len(sleeps) == 2
