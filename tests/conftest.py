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
def client(tmp_path_factory):
    """Synchronous test client for FastAPI app, backed by a throwaway database."""
    from fastapi.testclient import TestClient

    from src.config import settings
    from src.main import app

    webhook_secret = settings.GITHUB_WEBHOOK_SECRET
    database_url = settings.DATABASE_URL
    settings.GITHUB_WEBHOOK_SECRET = None
    # Claims and reviews persist, so tests must never share the development database.
    settings.DATABASE_URL = f"sqlite:///{tmp_path_factory.mktemp('db') / 'test.db'}"
    try:
        with TestClient(app) as c:
            yield c
    finally:
        settings.GITHUB_WEBHOOK_SECRET = webhook_secret
        settings.DATABASE_URL = database_url
