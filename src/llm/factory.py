"""LLM provider selection from application settings."""

from src.config import Settings
from src.llm.base import LLMProvider
from src.llm.fake import FakeLLMProvider
from src.llm.groq_provider import GroqProvider


def create_llm_provider(settings: Settings) -> LLMProvider:
    """Build the configured LLM provider."""
    match settings.LLM_PROVIDER:
        case "fake":
            return FakeLLMProvider()
        case "groq":
            if not settings.GROQ_API_KEY:
                raise RuntimeError("GROQ_API_KEY is required when LLM_PROVIDER=groq")
            return GroqProvider(api_key=settings.GROQ_API_KEY)

    raise RuntimeError(f"Unsupported LLM_PROVIDER: {settings.LLM_PROVIDER}")
