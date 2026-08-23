"""Tests for installation-scoped GitHub App token caching."""

from datetime import datetime, timedelta, timezone

import pytest

import src.github.auth as auth_module
from src.github.auth import GitHubAppAuth, _CachedInstallationToken


class _FakeTokenResponse:
    status_code = 201
    text = ""

    def __init__(self, installation_id: int) -> None:
        self._installation_id = installation_id

    def json(self) -> dict[str, str]:
        return {
            "token": f"token-for-{self._installation_id}",
            "expires_at": "2099-01-01T00:00:00Z",
        }


class _FakeAsyncClient:
    requested_installations: list[int] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return None

    async def post(self, url: str, headers: dict[str, str]):
        installation_id = int(url.split("/installations/", 1)[1].split("/", 1)[0])
        self.requested_installations.append(installation_id)
        return _FakeTokenResponse(installation_id)


@pytest.mark.asyncio
async def test_token_cache_is_scoped_by_installation(monkeypatch):
    auth = GitHubAppAuth()
    _FakeAsyncClient.requested_installations = []
    monkeypatch.setattr(auth, "generate_jwt", lambda: "signed-app-jwt")
    monkeypatch.setattr(auth_module.httpx, "AsyncClient", _FakeAsyncClient)

    assert await auth.get_installation_token(101) == "token-for-101"
    assert await auth.get_installation_token(202) == "token-for-202"
    assert await auth.get_installation_token(101) == "token-for-101"
    assert _FakeAsyncClient.requested_installations == [101, 202]


def test_clear_cache_can_target_one_installation():
    auth = GitHubAppAuth()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    auth._token_cache = {
        101: _CachedInstallationToken("token-for-101", expires_at),
        202: _CachedInstallationToken("token-for-202", expires_at),
    }

    auth.clear_cache(101)

    assert 101 not in auth._token_cache
    assert auth._token_cache[202].token == "token-for-202"
