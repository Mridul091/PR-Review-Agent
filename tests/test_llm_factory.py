import pytest
from pydantic import ValidationError

from src.config import Settings
from src.llm.factory import create_llm_provider
from src.llm.fake import FakeLLMProvider
from src.llm.groq_proivder import GroqProvider


def test_fake_provider_can_be_selected_without_groq_key():
    configured = Settings(_env_file=None, LLM_PROVIDER="fake", GROQ_API_KEY=None)

    provider = create_llm_provider(configured)

    assert isinstance(provider, FakeLLMProvider)


def test_groq_provider_requires_api_key():
    configured = Settings(_env_file=None, LLM_PROVIDER="groq", GROQ_API_KEY=None)

    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        create_llm_provider(configured)


def test_groq_provider_can_be_selected_explicitly():
    configured = Settings(_env_file=None, LLM_PROVIDER="groq", GROQ_API_KEY="test-key")

    provider = create_llm_provider(configured)

    assert isinstance(provider, GroqProvider)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
def test_unsupported_providers_are_rejected(provider):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LLM_PROVIDER=provider)


def test_fake_provider_is_not_allowed_in_production():
    with pytest.raises(ValidationError, match="LLM_PROVIDER=fake"):
        Settings(
            _env_file=None,
            ENVIRONMENT="production",
            REVIEW_API_TOKEN="token",
            GITHUB_WEBHOOK_SECRET="secret",
            LLM_PROVIDER="fake",
        )
