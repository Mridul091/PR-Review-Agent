"""Tests for the FastAPI server and webhook endpoint — Phase 1."""
import json


def test_health_check(client):
    """GET /health should return 200 with healthy status."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "pr-reviewer-agent"
    assert "environment" in data


def test_webhook_pr_opened_returns_202(client):
    """
    POST /api/v1/webhook with a pull_request.opened payload should return 202.
    Signature check is skipped because GITHUB_WEBHOOK_SECRET is not set in test env.
    """
    payload = {
        "action": "opened",
        "pull_request": {"number": 42, "title": "feat: add login endpoint"},
        "repository": {"full_name": "test-owner/test-repo"},
    }
    response = client.post(
        "/api/v1/webhook",
        content=json.dumps(payload),
        headers={"X-GitHub-Event": "pull_request", "Content-Type": "application/json"},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    assert data["pr_number"] == 42
    assert data["repo"] == "test-owner/test-repo"


def test_webhook_pr_synchronize_returns_202(client):
    """pull_request.synchronize (new commit) should also be accepted."""
    payload = {
        "action": "synchronize",
        "pull_request": {"number": 7, "title": "fix: null pointer"},
        "repository": {"full_name": "org/my-service"},
    }
    response = client.post(
        "/api/v1/webhook",
        content=json.dumps(payload),
        headers={"X-GitHub-Event": "pull_request", "Content-Type": "application/json"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "accepted"


def test_webhook_ignored_event_returns_202_ignored(client):
    """
    A non-PR event (e.g., push) should return 202 with status:ignored.
    """
    payload = {"ref": "refs/heads/main", "repository": {"full_name": "org/repo"}}
    response = client.post(
        "/api/v1/webhook",
        content=json.dumps(payload),
        headers={"X-GitHub-Event": "push", "Content-Type": "application/json"},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "ignored"


def test_webhook_pr_closed_is_ignored(client):
    """pull_request.closed should be ignored (we don't review closed PRs)."""
    payload = {
        "action": "closed",
        "pull_request": {"number": 99},
        "repository": {"full_name": "org/repo"},
    }
    response = client.post(
        "/api/v1/webhook",
        content=json.dumps(payload),
        headers={"X-GitHub-Event": "pull_request", "Content-Type": "application/json"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
