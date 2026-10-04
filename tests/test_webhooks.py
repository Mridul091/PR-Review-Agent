"""Tests for the FastAPI server and webhook endpoint — Phase 1."""

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.webhooks import router
from src.config import settings
from src.review.store import InMemoryReviewStore


def reviewable_payload(number=42):
    return {
        "action": "opened",
        "pull_request": {
            "number": number,
            "title": "feat: add login endpoint",
            "base": {"sha": "a" * 40},
            "head": {"sha": "b" * 40},
        },
        "repository": {"full_name": "test-owner/test-repo"},
        "installation": {"id": 1234},
    }


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
    payload = reviewable_payload()
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


def test_webhook_uses_review_service_and_persists_result():
    class ReviewService:
        async def review(self, request):
            return SimpleNamespace(
                review_id="webhook-review-123",
                started_at=datetime.now(timezone.utc),
                request=request,
                detector_response=None,
                findings=[],
                failure=None,
                model_calls=0,
            )

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.review_service = ReviewService()
    app.state.review_store = InMemoryReviewStore()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhook",
            json=reviewable_payload(),
            headers={"X-GitHub-Event": "pull_request"},
        )

    assert response.status_code == 202
    persisted = asyncio.run(app.state.review_store.get_result("webhook-review-123"))
    assert persisted is not None
    assert persisted.request.repository == "test-owner/test-repo"
    assert persisted.request.installation_id == 1234
    assert persisted.request.pull_request_number == 42


def test_webhook_pr_synchronize_returns_202(client):
    """pull_request.synchronize (new commit) should also be accepted."""
    payload = {
        "action": "synchronize",
        "pull_request": {
            "number": 7,
            "title": "fix: null pointer",
            "base": {"sha": "a" * 40},
            "head": {"sha": "b" * 40},
        },
        "repository": {"full_name": "org/my-service"},
        "installation": {"id": 1234},
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


def test_reviewable_webhook_validates_required_payload(client):
    response = client.post(
        "/api/v1/webhook",
        json={"action": "opened", "pull_request": {"number": 1}},
        headers={"X-GitHub-Event": "pull_request"},
    )
    assert response.status_code == 422


def test_webhook_rejects_invalid_json(client):
    response = client.post(
        "/api/v1/webhook",
        content="{",
        headers={"X-GitHub-Event": "pull_request", "Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_webhook_requires_valid_signature_when_configured(client, monkeypatch):
    secret = "test-webhook-secret"
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", secret)
    body = json.dumps(reviewable_payload()).encode()

    missing = client.post(
        "/api/v1/webhook",
        content=body,
        headers={"X-GitHub-Event": "pull_request"},
    )
    assert missing.status_code == 401

    invalid = client.post(
        "/api/v1/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=invalid",
        },
    )
    assert invalid.status_code == 401

    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    valid = client.post(
        "/api/v1/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )
    assert valid.status_code == 202


def test_webhook_rejects_oversized_payload(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_WEBHOOK_BYTES", 10)
    response = client.post(
        "/api/v1/webhook",
        json=reviewable_payload(),
        headers={"X-GitHub-Event": "pull_request"},
    )
    assert response.status_code == 413
