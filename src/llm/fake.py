"""Deterministic LLM provider used by tests and local development."""

from collections import deque
from collections.abc import Sequence

from src.llm.base import LLMProvider, ModelUsage, ReviewModelRequest, ReviewResponseModel


class FakeLLMProvider(LLMProvider):
    """Return queued responses while recording every request received."""

    def __init__(
        self,
        responses: Sequence[ReviewResponseModel] | None = None,
    ) -> None:
        self._responses = deque(responses or [])
        self._requests: list[ReviewModelRequest] = []

    async def generate_review(
        self,
        request: ReviewModelRequest,
    ) -> ReviewResponseModel:
        self._requests.append(request)

        if self._responses:
            return self._responses.popleft().model_copy(deep=True)

        return ReviewResponseModel(
            draft_findings=[],
            usage=ModelUsage(
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                estimated_cost_usd=0,
            ),
            model_name=request.model_name,
            finish_reason="completed",
        )

    @property
    def requests(self) -> list[ReviewModelRequest]:
        """Requests received by this provider, in call order."""
        return self._requests

    @property
    def call_count(self) -> int:
        """Number of provider calls received."""
        return len(self._requests)

    def reset(self) -> None:
        """Clear recorded requests and queued responses."""
        self._responses.clear()
        self._requests.clear()
