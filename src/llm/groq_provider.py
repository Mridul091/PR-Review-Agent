import json

from groq import APIConnectionError, APIStatusError, AsyncGroq
from pydantic import ValidationError

from src.llm.base import (
    FinishReason,
    LLMProvider,
    MalformedProviderOutputError,
    ModelUsage,
    ReviewModelRequest,
    ReviewResponseModel,
    TransientProviderError,
)


class GroqProvider(LLMProvider):
    def __init__(self, api_key: str):
        # Retries are owned by Review so the attempt and elapsed-time budgets
        # cannot be bypassed by the SDK's internal retry policy.
        self._client = AsyncGroq(api_key=api_key, max_retries=0)

    async def generate_review(self, request: ReviewModelRequest) -> ReviewResponseModel:
        try:
            response = await self._client.chat.completions.create(
                model=request.model_name,
                messages=[
                    {"role": "system", "content": request.prompt},
                    {"role": "user", "content": request.diff_context},
                ],
                max_tokens=request.output_constraints.max_output_tokens,
                response_format={"type": "json_object"},
            )
        except APIConnectionError as exc:
            raise TransientProviderError("Provider connection failed") from exc
        except APIStatusError as exc:
            if exc.status_code == 429 or exc.status_code >= 500:
                raise TransientProviderError("Provider temporarily unavailable") from exc
            raise

        try:
            content = response.choices[0].message.content
            payload = json.loads(content) if content else None
            if not isinstance(payload, dict) or "draft_findings" not in payload:
                raise MalformedProviderOutputError("Missing draft_findings array")
            usage = response.usage
            return ReviewResponseModel(
                draft_findings=payload["draft_findings"],
                usage=ModelUsage(
                    prompt_tokens=usage.prompt_tokens if usage else 0,
                    completion_tokens=usage.completion_tokens if usage else 0,
                    total_tokens=usage.total_tokens if usage else 0,
                    estimated_cost_usd=0,
                ),
                model_name=request.model_name,
                finish_reason=self._map_finish_reason(response.choices[0].finish_reason),
            )
        except (
            ValueError,
            IndexError,
            KeyError,
            TypeError,
            AttributeError,
            ValidationError,
        ) as exc:
            raise MalformedProviderOutputError("Provider returned invalid review data") from exc

    async def aclose(self) -> None:
        """Close the underlying async HTTP client during application shutdown."""
        await self._client.close()

    @staticmethod
    def _map_finish_reason(reason: str | None) -> FinishReason:
        if reason == "stop":
            return "completed"
        if reason == "length":
            return "length_limit"
        if reason == "content_filter":
            return "content_filtered"
        return "other"
