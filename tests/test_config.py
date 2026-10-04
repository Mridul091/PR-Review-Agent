"""Configuration safety tests."""

import pytest
from pydantic import ValidationError

from src.config import Settings


def test_production_requires_webhook_secret():
    with pytest.raises(ValidationError, match="GITHUB_WEBHOOK_SECRET is required"):
        Settings(_env_file=None, ENVIRONMENT="production", GITHUB_WEBHOOK_SECRET=None)


def test_local_development_can_omit_webhook_secret():
    configured = Settings(
        _env_file=None,
        ENVIRONMENT="development",
        GITHUB_WEBHOOK_SECRET=None,
    )
    assert configured.GITHUB_WEBHOOK_SECRET is None


def test_limits_must_be_positive():
    with pytest.raises(ValidationError, match="MAX_PR_FILES"):
        Settings(_env_file=None, MAX_PR_FILES=0)


def test_repository_allowlist_loads_from_environment(monkeypatch):
    monkeypatch.setenv("REVIEW_REPOSITORY_ACCESS", '{"1234":["owner/repo"]}')
    configured = Settings(_env_file=None)
    assert configured.REVIEW_REPOSITORY_ACCESS == {1234: ["owner/repo"]}


def test_model_attempts_must_be_bounded():
    with pytest.raises(ValidationError, match="MAX_MODEL_ATTEMPTS"):
        Settings(_env_file=None, MAX_MODEL_ATTEMPTS=0)
    with pytest.raises(ValidationError, match="MAX_MODEL_ATTEMPTS"):
        Settings(_env_file=None, MAX_MODEL_ATTEMPTS=4)


def test_repository_allowlist_rejects_invalid_entries():
    with pytest.raises(ValidationError, match="positive installation IDs"):
        Settings(_env_file=None, REVIEW_REPOSITORY_ACCESS={0: ["owner/repo"]})
    with pytest.raises(ValidationError, match="owner/repo"):
        Settings(_env_file=None, REVIEW_REPOSITORY_ACCESS={1234: ["not-a-repo"]})
