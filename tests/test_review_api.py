from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.review import router
from src.middleware.auth import AuthenticationMiddleware
from src.review.schemas import ReviewFailure, ReviewRequest


class _ReviewService:
    def __init__(self):
        self.calls = 0
        self.failure = None

    async def review(self, request: ReviewRequest):
        self.calls += 1
        return SimpleNamespace(
            review_id="review-123",
            started_at=datetime.now(timezone.utc),
            request=request,
            detector_response=None,
            findings=[],
            failure=self.failure,
            model_calls=0,
        )


def _test_client(allowed=frozenset({(12, "owner/repo")})) -> TestClient:
    app = FastAPI()
    app.add_middleware(AuthenticationMiddleware)
    app.include_router(router, prefix="/api/v1")
    app.state.review_service = _ReviewService()
    app.state.review_api_token = "test-token"
    app.state.review_allowed_repositories = allowed
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


def _request_body() -> dict:
    return {
        "repository": "owner/repo",
        "installation_id": 12,
        "pull_request_number": 4,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
    }


def test_create_review_can_be_retrieved_by_id():
    with _test_client() as client:
        created = client.post("/api/v1/reviews", json=_request_body())
        assert created.status_code == 200
        review_id = created.json()["review_id"]

        retrieved = client.get(f"/api/v1/reviews/{review_id}")

    assert retrieved.status_code == 200
    assert retrieved.json() == created.json()


def test_rejected_review_is_retrievable_with_typed_budget_failure():
    with _test_client() as client:
        client.app.state.review_service.failure = ReviewFailure(
            code="review_context_too_large",
            stage="input",
            message="Python changes exceed the configured review context limit.",
        )
        created = client.post("/api/v1/reviews", json=_request_body())
        assert created.status_code == 200
        assert created.json()["status"] == "rejected"
        assert created.json()["failures"][0]["code"] == "review_context_too_large"
        assert created.json()["usage"]["model_calls"] == 0
        retrieved = client.get(f"/api/v1/reviews/{created.json()['review_id']}")
        assert retrieved.json() == created.json()


def test_get_review_returns_404_for_unknown_id():
    with _test_client() as client:
        response = client.get("/api/v1/reviews/not-found")

    assert response.status_code == 404
    assert response.json()["detail"] == "Review not found."


def test_review_routes_require_valid_token():
    with _test_client() as client:
        for headers in ({"Authorization": ""}, {"Authorization": "Bearer wrong"}):
            response = client.post("/api/v1/reviews", json=_request_body(), headers=headers)
            assert response.status_code == 401
            assert client.get("/api/v1/reviews/review-123", headers=headers).status_code == 401
        assert client.app.state.review_service.calls == 0


def test_post_denies_other_repositories_and_installations_without_calling_service():
    with _test_client() as client:
        for changes in ({"repository": "other/repo"}, {"installation_id": 13}):
            response = client.post("/api/v1/reviews", json={**_request_body(), **changes})
            assert response.status_code == 403
        assert client.app.state.review_service.calls == 0


def test_empty_allowlist_denies_by_default():
    with _test_client(frozenset()) as client:
        response = client.post("/api/v1/reviews", json=_request_body())
        assert response.status_code == 403
        assert client.app.state.review_service.calls == 0


def test_repository_name_is_case_insensitive():
    with _test_client() as client:
        response = client.post(
            "/api/v1/reviews", json={**_request_body(), "repository": "Owner/Repo"}
        )
        assert response.status_code == 200


def test_get_review_rechecks_permissions_even_after_creation():
    with _test_client() as client:
        created = client.post("/api/v1/reviews", json=_request_body())
        assert created.status_code == 200
        client.app.state.review_allowed_repositories = frozenset()
        response = client.get(f"/api/v1/reviews/{created.json()['review_id']}")

    assert response.status_code == 404
