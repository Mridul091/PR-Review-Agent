"""
Pytest configuration and shared fixtures for Phase 1 tests.
"""
import pytest


@pytest.fixture(scope="session")
def client():
    """Synchronous test client for FastAPI app."""
    from fastapi.testclient import TestClient

    from src.main import app

    with TestClient(app) as c:
        yield c
