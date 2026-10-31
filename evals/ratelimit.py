"""Wait out provider rate limits so they do not show up as review failures."""

import asyncio

from src.llm.base import LLMProvider, RateLimitedError, ReviewModelRequest, ReviewResponseModel

MAX_WAIT_SECONDS = 65.0


class RateLimitRetryProvider(LLMProvider):
    """Retry a rate-limited call after the provider's advised wait.

    Free-tier limits (for example 8,000 tokens per minute) would otherwise turn
    most reviews into provider errors and hide the model's real behaviour.
    """

    def __init__(self, inner: LLMProvider, *, max_waits: int = 2) -> None:
        self._inner = inner
        self._max_waits = max_waits
        self.waits = 0
        self.seconds_waited = 0.0

    async def generate_review(self, request: ReviewModelRequest) -> ReviewResponseModel:
        for attempt in range(self._max_waits + 1):
            try:
                return await self._inner.generate_review(request)
            except RateLimitedError as exc:
                if attempt == self._max_waits:
                    raise
                delay = min(exc.retry_after or 15.0, MAX_WAIT_SECONDS) + 0.5
                self.waits += 1
                self.seconds_waited += delay
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if close is not None:
            await close()
