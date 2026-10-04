from datetime import datetime, timezone
from typing import Annotated, cast

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

from src.middleware.auth import ReviewPrincipal
from src.review.schemas import ReviewFailure, ReviewRequest, ReviewResult, ReviewStatus, ReviewUsage
from src.review.service import ReviewRun, ReviewService
from src.review.store import InMemoryReviewStore, ReviewStore

router = APIRouter(prefix="/reviews", tags=["Reviews"])


def get_review_service(request: Request) -> ReviewService:
    app = cast(FastAPI, request.app)
    return cast(ReviewService, app.state.review_service)


def get_review_store(request: Request) -> ReviewStore:
    app = cast(FastAPI, request.app)
    if not hasattr(app.state, "review_store"):
        app.state.review_store = InMemoryReviewStore()
    return cast(ReviewStore, app.state.review_store)


def get_review_principal(request: Request) -> ReviewPrincipal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, ReviewPrincipal):
        raise HTTPException(status_code=401, detail="Authentication required.")
    return principal


def _has_repository_access(principal: ReviewPrincipal, review_request: ReviewRequest) -> bool:
    return (
        review_request.installation_id,
        review_request.repository.casefold(),
    ) in principal.allowed_repositories


def authorize_review_request(
    review_request: ReviewRequest,
    principal: Annotated[ReviewPrincipal, Depends(get_review_principal)],
) -> ReviewRequest:
    if not _has_repository_access(principal, review_request):
        raise HTTPException(status_code=403, detail="Repository access denied.")
    return review_request


@router.post("", response_model=ReviewResult)
async def create_review(
    review_request: Annotated[ReviewRequest, Depends(authorize_review_request)],
    http_request: Request,
    service: Annotated[ReviewService, Depends(get_review_service)],
) -> ReviewResult:
    run = await service.review(review_request)
    result = build_review_result(run)
    await get_review_store(http_request).save_result(result)
    return result


@router.get("/{review_id}", response_model=ReviewResult)
async def get_review(
    review_id: str,
    request: Request,
    principal: Annotated[ReviewPrincipal, Depends(get_review_principal)],
) -> ReviewResult:
    """Retrieve a persisted review result, if authorized."""
    result = await get_review_store(request).get_result(review_id)
    if result is None or not _has_repository_access(principal, result.request):
        raise HTTPException(status_code=404, detail="Review not found.")
    return result


def build_review_result(
    run: ReviewRun,
    finished_at: datetime | None = None,
) -> ReviewResult:
    finished_at = finished_at or datetime.now(timezone.utc)

    duration_ms = max(
        0,
        int((finished_at - run.started_at).total_seconds() * 1000),
    )

    response = run.detector_response
    agents_attempted = ["bug_detector"] if run.model_calls else []
    agents_failed: list[str] = []
    failures: list[ReviewFailure] = []
    status = ReviewStatus.COMPLETED
    usage = ReviewUsage(model_calls=run.model_calls)

    if run.failure is not None:
        failures.append(run.failure)
        status = ReviewStatus.REJECTED if run.failure.stage == "input" else ReviewStatus.FAILED
        if run.failure.agent is not None and run.failure.agent in agents_attempted:
            agents_failed.append(run.failure.agent)

    if response is not None:
        usage = ReviewUsage(
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            model_calls=run.model_calls,
            estimated_cost_usd=response.usage.estimated_cost_usd or 0,
        )

        if response.finish_reason != "completed":
            agents_failed.append("bug_detector")

            failure_code = {
                "length_limit": "model_output_limit",
                "content_filtered": "content_filtered",
                "provider_error": "provider_error",
                "other": "provider_unknown",
            }[response.finish_reason]

            failures.append(
                ReviewFailure(
                    code=failure_code,
                    stage="review",
                    message="The bug detector did not complete successfully",
                    retryable=response.finish_reason in {"provider_error", "other"},
                    agent="bug_detector",
                )
            )

            status = (
                ReviewStatus.PARTIAL
                if response.finish_reason == "length_limit"
                else ReviewStatus.FAILED
            )

    return ReviewResult(
        review_id=run.review_id,
        request=run.request,
        status=status,
        findings=run.findings,
        agents_attempted=agents_attempted,
        agents_failed=agents_failed,
        failures=failures,
        started_at=run.started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        usage=usage,
    )
