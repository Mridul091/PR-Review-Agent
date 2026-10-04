"""GitHub ingestion tests using an in-process mock HTTP transport."""

import httpx
import pytest

import src.github.client as client_module
from src.github.client import GitHubClient
from src.github.errors import GitHubClientError, InputLimitExceededError


class FakeAuth:
    def get_auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}


def install_transport(monkeypatch, handler):
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def build_client(**kwargs):
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", build_client)


@pytest.mark.asyncio
async def test_pull_request_keeps_target_and_fork_identity(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "number": 8,
                "title": "Fork change",
                "body": None,
                "state": "open",
                "user": {"login": "dev", "avatar_url": "avatar", "html_url": "user"},
                "base": {
                    "ref": "main",
                    "sha": "a" * 40,
                    "repo": {"full_name": "target/project"},
                },
                "head": {
                    "ref": "feature",
                    "sha": "b" * 40,
                    "repo": {"full_name": "contributor/project"},
                },
                "html_url": "pr",
                "draft": False,
                "changed_files": 1,
                "additions": 1,
                "deletions": 0,
            },
        )

    install_transport(monkeypatch, handler)
    metadata = await GitHubClient(FakeAuth()).get_pull_request("target/project", 8, "token")
    assert metadata.target_repo_full_name == "target/project"
    assert metadata.repo_full_name == "target/project"
    assert metadata.head_repo_full_name == "contributor/project"
    assert metadata.base_sha == "a" * 40


@pytest.mark.asyncio
async def test_pull_request_allows_deleted_head_repository(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "number": 8,
                "title": "Deleted fork",
                "body": "",
                "state": "open",
                "user": {"login": "dev", "avatar_url": "avatar", "html_url": "user"},
                "base": {
                    "ref": "main",
                    "sha": "a" * 40,
                    "repo": {"full_name": "target/project"},
                },
                "head": {"ref": "feature", "sha": "b" * 40, "repo": None},
                "html_url": "pr",
                "changed_files": 1,
            },
        )

    install_transport(monkeypatch, handler)
    metadata = await GitHubClient(FakeAuth()).get_pull_request("target/project", 8, "token")
    assert metadata.head_repo_full_name is None


@pytest.mark.asyncio
async def test_get_pr_files_paginates_and_marks_unavailable_patches(monkeypatch):
    requested_pages = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        requested_pages.append(page)
        if page == 1:
            items = [
                {
                    "filename": f"src/file_{index}.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "patch": "+line",
                }
                for index in range(100)
            ]
        else:
            items = [
                {
                    "filename": "asset.png",
                    "status": "modified",
                    "additions": 0,
                    "deletions": 0,
                }
            ]
        return httpx.Response(200, json=items)

    install_transport(monkeypatch, handler)
    files = await GitHubClient(FakeAuth()).get_pr_files("owner/repo", 1, "token")
    assert requested_pages == [1, 2]
    assert len(files) == 101
    assert files[-1].patch_status == "unavailable"


@pytest.mark.asyncio
async def test_get_pr_files_rejects_file_limit(monkeypatch):
    monkeypatch.setattr(client_module.settings, "MAX_PR_FILES", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"filename": "a.py", "status": "modified", "patch": "+a"},
                {"filename": "b.py", "status": "modified", "patch": "+b"},
            ],
        )

    install_transport(monkeypatch, handler)
    with pytest.raises(InputLimitExceededError, match="1-file limit") as caught:
        await GitHubClient(FakeAuth()).get_pr_files("owner/repo", 1, "token")
    assert caught.value.code == "too_many_files"
    assert caught.value.to_failure().stage == "input"


@pytest.mark.asyncio
async def test_get_pr_files_rejects_oversized_patch(monkeypatch):
    monkeypatch.setattr(client_module.settings, "MAX_PATCH_BYTES", 3)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"filename": "a.py", "status": "modified", "patch": "+long"}],
        )

    install_transport(monkeypatch, handler)
    with pytest.raises(InputLimitExceededError) as caught:
        await GitHubClient(FakeAuth()).get_pr_files("owner/repo", 1, "token")
    assert caught.value.code == "patch_too_large"


@pytest.mark.asyncio
async def test_get_pr_diff_rejects_oversized_response(monkeypatch, diff_fixture):
    monkeypatch.setattr(client_module.settings, "MAX_DIFF_BYTES", 5)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=diff_fixture("oversized"))

    install_transport(monkeypatch, handler)
    with pytest.raises(InputLimitExceededError) as caught:
        await GitHubClient(FakeAuth()).get_pr_diff("owner/repo", 1, "token")
    assert caught.value.code == "diff_too_large"


@pytest.mark.asyncio
async def test_http_failure_is_typed_and_safe(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="sensitive upstream response")

    install_transport(monkeypatch, handler)
    with pytest.raises(GitHubClientError) as caught:
        await GitHubClient(FakeAuth()).get_pull_request("owner/repo", 1, "token")
    assert caught.value.code == "github_http_error"
    assert caught.value.retryable
    assert "sensitive" not in str(caught.value)


@pytest.mark.asyncio
async def test_timeout_is_typed_and_retryable(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    install_transport(monkeypatch, handler)
    with pytest.raises(GitHubClientError) as caught:
        await GitHubClient(FakeAuth()).get_pull_request("owner/repo", 1, "token")
    assert caught.value.code == "github_timeout"
    assert caught.value.retryable


@pytest.mark.asyncio
async def test_pull_request_rejects_target_repository_mismatch(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "base": {"repo": {"full_name": "different/repo"}},
            },
        )

    install_transport(monkeypatch, handler)
    with pytest.raises(GitHubClientError) as caught:
        await GitHubClient(FakeAuth()).get_pull_request("owner/repo", 1, "token")
    assert caught.value.code == "repository_identity_mismatch"
