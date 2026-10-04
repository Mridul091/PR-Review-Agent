"""Pytest configuration and shared fixtures."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def diff_fixture():
    """Load a named unified-diff fixture."""

    def load(name: str) -> str:
        return (FIXTURES / "diffs" / f"{name}.diff").read_text()

    return load


@pytest.fixture(scope="session")
def client():
    """Synchronous test client for FastAPI app."""
    from fastapi.testclient import TestClient

    from src.config import settings
    from src.main import app

    webhook_secret = settings.GITHUB_WEBHOOK_SECRET
    settings.GITHUB_WEBHOOK_SECRET = None
    try:
        with TestClient(app) as c:
            yield c
    finally:
        settings.GITHUB_WEBHOOK_SECRET = webhook_secret
